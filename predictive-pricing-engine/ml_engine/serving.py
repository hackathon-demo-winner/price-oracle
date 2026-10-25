"""Scrape-free forecast handler for the deployed function (see main.py).

Runs the shipped ONNX weights with onnxruntime + numpy only: no LightGBM, pandas or aiohttp,
and no context.dev call. The caller (the Node functions) has already scraped the product and
posts the price; this module turns it into the quantile forecast.

`handle_forecast(method, body)` returns `(http_status, json_dict)` and never raises.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from .config import FEATURES, GEMINI_BUDGET_S, GEMINI_CACHE_TTL_S, HORIZON_DAYS, RETAILERS, WEIGHTS_DIR
from .data_pipeline.feature_engineering import FeatureInputs, build_features, classify_category
from .data_pipeline.gemini_prediction import BASE_URL, EVENTS_PATH, build_macro_signals
from .data_pipeline.historical_fetcher import CategoryIndexStore
from .model.quantile_regressor import apply_conformal, forecast_from_log_ratios

ONNX_DIR = WEIGHTS_DIR / "product_30d" / "onnx"
QUANTILE_FILES = ("p5", "p25", "p50", "p75", "p95")
MAX_PRICE = 1_000_000.0

_RETAILER_ALIASES = {"best buy": "bestbuy", "best_buy": "bestbuy", "b&h": "bhphoto", "bh": "bhphoto",
                     "bhphotovideo": "bhphoto", "pcpp": "pcpartpicker"}


class OnnxQuantileModel:
    """The five exported boosters behind one predict call."""

    def __init__(self, directory: Path = ONNX_DIR):
        import onnxruntime as ort

        export = json.loads((directory / "onnx_export.json").read_text(encoding="utf-8"))
        meta = json.loads((directory.parent / "metadata.json").read_text(encoding="utf-8"))
        if tuple(export["features"]) != FEATURES:
            raise RuntimeError("ONNX weights were exported with a different feature list; re-export")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        digest = hashlib.sha256()
        self.sessions = []
        for name in QUANTILE_FILES:
            raw = (directory / f"{name}.onnx").read_bytes()
            digest.update(raw)
            self.sessions.append(ort.InferenceSession(raw, sess_options=options, providers=["CPUExecutionProvider"]))
        self.input_name = export["input_name"]
        self.offsets = export["conformal_offsets"]
        self.name = meta.get("model", "product_30d")
        self.trained_at = meta.get("trained_at")
        self.trained_on = "synthetic" if meta.get("training_data_is_synthetic", True) else "real"
        self.version = f"{(self.trained_at or 'unknown')[:10]}-{digest.hexdigest()[:8]}"

    def predict_log_ratio(self, vector: np.ndarray) -> np.ndarray:
        row = np.asarray(vector, dtype=np.float32).reshape(1, -1)
        raw = np.array([[float(s.run(None, {self.input_name: row})[0].reshape(-1)[0]) for s in self.sessions]])
        return apply_conformal(raw, self.offsets)[0]


# ----------------------------------------------------------------------------- Gemini (display only)

def _fetch_economics_events(timeout_s: float) -> list[dict]:
    url = BASE_URL + EVENTS_PATH + "?" + urllib.parse.urlencode({"category": "Economics", "status": "active", "limit": 100})
    request = urllib.request.Request(url, headers={"User-Agent": "priceoracle-ml/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout_s) as resp:
        return list(json.loads(resp.read().decode("utf-8")).get("data") or [])


class MacroCache:
    """Gemini display signals with a 15-minute cache. A failed or slow refresh returns the
    last good value if there is one, else None; it is retried at most once a minute so a
    Gemini outage costs one slow request per minute, not every request."""

    def __init__(self, fetch: Callable[[float], list[dict]] = _fetch_economics_events,
                 ttl_s: float = GEMINI_CACHE_TTL_S, budget_s: float = GEMINI_BUDGET_S, retry_after_s: float = 60.0,
                 clock: Callable[[], float] = time.monotonic):
        self._fetch, self._ttl_s, self._budget_s, self._retry_after_s, self._clock = fetch, ttl_s, budget_s, retry_after_s, clock
        self._value: Optional[dict] = None
        self._fetched_at: Optional[float] = None
        self._failed_at: Optional[float] = None
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gemini")
        self.network_fetches = 0

    def get(self) -> Optional[dict]:
        now = self._clock()
        if self._fetched_at is not None and now - self._fetched_at < self._ttl_s:
            return self._value
        if self._failed_at is not None and now - self._failed_at < self._retry_after_s:
            return self._value
        if not self._lock.acquire(blocking=False):
            return self._value  # another request is refreshing; do not queue behind it
        try:
            self.network_fetches += 1
            events = self._pool.submit(self._fetch, self._budget_s).result(timeout=self._budget_s)
            signals = {k: (None if v is None else round(v, 4)) for k, v in build_macro_signals(events).items()}
            self._value = {"source": "Gemini Prediction Markets", "signals": signals,
                           "fetchedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "usedByModel": False}
            self._fetched_at, self._failed_at = self._clock(), None
        except Exception:
            self._failed_at = self._clock()
        finally:
            self._lock.release()
        return self._value


# ----------------------------------------------------------------------------- handler

_model: Optional[OnnxQuantileModel] = None
_cpi: Optional[CategoryIndexStore] = None
_macro = MacroCache()
_load_lock = threading.Lock()


def load() -> tuple[OnnxQuantileModel, CategoryIndexStore]:
    global _model, _cpi
    if _model is None or _cpi is None:
        with _load_lock:
            if _model is None:
                _model = OnnxQuantileModel()
            if _cpi is None:
                _cpi = CategoryIndexStore()
    return _model, _cpi


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _error(status: int, code: str, message: str) -> tuple[int, dict]:
    return status, {"success": False, "error": {"code": code, "message": message}}


def normalize_retailer(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = _RETAILER_ALIASES.get(text, text.replace(" ", ""))
    return text if text in RETAILERS else "other"


MACRO_TILT_CAP = 0.015  # the macro heuristic may move the forecast by at most 1.5% either way


def macro_tilt(signals: Optional[dict]) -> float:
    """Small heuristic shift of the whole forecast from Gemini prediction-market odds.

    This is a hand-set rule, not a trained input: the model has no history of these
    contracts to learn from. Rate-hike and growth odds nudge prices up; rate-cut,
    recession and unemployment odds nudge them down. A missing signal contributes nothing.
    """
    if not signals:
        return 0.0

    def odds(name: str, neutral: float = 0.0) -> float:
        value = signals.get(name)
        return neutral if value is None else float(value)

    score = (0.5 * (odds('fed_hike_probability_next_meeting') - odds('fed_cut_probability_next_meeting'))
             + 0.5 * (odds('gdp_growth_above_2pct_probability', 0.5) - 0.5)
             - 0.5 * odds('recession_probability')
             - 0.5 * odds('unemployment_above_5pct_probability'))
    return max(-MACRO_TILT_CAP, min(MACRO_TILT_CAP, 0.02 * score))


def subtitle_for(action: str, delta_percent: float, current: float, p25: float, p75: float) -> str:
    if action == "WAIT":
        if delta_percent <= -0.05:
            return f"Model median is {abs(delta_percent):.1f}% lower in {HORIZON_DAYS} days"
        return f"Priced at or above the model's typical {HORIZON_DAYS}-day range"
    if action == "BUY NOW":
        if delta_percent >= 0.05:
            return f"Model median is {delta_percent:.1f}% higher in {HORIZON_DAYS} days"
        return f"Priced at or below the model's typical {HORIZON_DAYS}-day range"
    return f"Within the model's typical {HORIZON_DAYS}-day range (${p25:,.2f} to ${p75:,.2f})"


def handle_forecast(method: str, body: Any, today: Optional[date] = None,
                    macro: Optional[MacroCache] = None) -> tuple[int, dict]:
    if (method or "").upper() != "POST":
        return _error(405, "method_not_allowed", "Use POST with a JSON body.")
    if not isinstance(body, dict):
        return _error(400, "invalid_json", "The request body must be a JSON object.")
    current = _number(body.get("currentPrice"))
    if current is None or current <= 0 or current > MAX_PRICE:
        return _error(400, "invalid_current_price", "currentPrice must be a positive number.")
    regular_raw = body.get("regularPrice")
    regular = _number(regular_raw)
    if regular_raw is not None and (regular is None or regular <= 0):
        return _error(400, "invalid_regular_price", "regularPrice must be a positive number or null.")
    if regular is not None and regular < current:
        regular = None  # a "regular" price below the selling price is not a list price
    for field in ("category", "retailer", "title", "productId"):
        if body.get(field) is not None and not isinstance(body[field], str):
            return _error(400, "invalid_field", f"{field} must be a string or null.")

    try:
        model, cpi = load()
        as_of = today or datetime.now(timezone.utc).date()
        retailer = normalize_retailer(body.get("retailer"))
        category = classify_category((body.get("title") or "")[:500], (body.get("category") or "")[:200])
        cpi_3m, cpi_12m = cpi.trend(category, as_of)
        features = build_features(FeatureInputs(
            current_price=current, as_of=as_of, retailer=retailer, category=category, regular_price=regular,
            category_cpi_3m_change=cpi_3m, category_cpi_12m_change=cpi_12m,
        ))  # no price history: every history feature is NaN (missing), not a default value
        vector = np.array([features[name] for name in FEATURES], dtype=np.float64)
        macro_block = (macro or _macro).get()
        tilt = macro_tilt(macro_block.get('signals') if macro_block else None)
        # Shifting every quantile by the same factor keeps them ordered and keeps the
        # recommendation consistent with the shifted range
        forecast = forecast_from_log_ratios(current, model.predict_log_ratio(vector) + np.log1p(tilt))
    except Exception as err:  # a structured failure, never a guessed forecast
        return _error(500, "model_error", f"{type(err).__name__}: {err}")

    delta = round(forecast.projected_delta_percent, 1)
    return 200, {
        "success": True,
        "forecast": {
            "currentPrice": round(current, 2),
            "quantiles": {"lowest": forecast.p5, "low": forecast.p25, "mid": forecast.p50,
                          "high": forecast.p75, "highest": forecast.p95},
            "projectedMid": forecast.p50,
            "deltaPercentMonth": delta + 0.0,  # normalizes -0.0
            "recommendation": forecast.action,
            "subtitle": subtitle_for(forecast.action, forecast.projected_delta_percent, current, forecast.p25, forecast.p75),
            "confidence": None,  # no honest basis: the model has not been validated on real prices
            "model": {"name": model.name, "version": model.version, "trainedOn": model.trained_on,
                      "trainedAt": model.trained_at, "horizonDays": HORIZON_DAYS, "historyMode": "snapshot_only",
                      "category": category, "retailer": retailer},
            "macro": None if macro_block is None else {**macro_block, "heuristicAdjustmentPercent": round(tilt * 100, 2) + 0.0},
        },
    }
