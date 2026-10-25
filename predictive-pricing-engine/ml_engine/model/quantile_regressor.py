"""Multi-quantile inference (P5, P25, P50, P75, P95) and the decision rule.

The five LightGBM boosters each predict one quantile of log(price in 30 days / price now).
Predictions are made non-crossing by sorting, widened or narrowed by conformal offsets
fitted on a held-out calibration slice, and converted back to prices.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

import numpy as np

try:  # LightGBM is a training / research dependency; the deployed function serves ONNX
    import lightgbm as lgb
except ImportError:  # pragma: no cover
    lgb = None

from ..config import FEATURES, QUANTILE_NAMES, QUANTILES, WEIGHTS_DIR

PRODUCT_MODEL_DIR = WEIGHTS_DIR / "product_30d"
CATEGORY_MODEL_DIR = WEIGHTS_DIR / "category_index_1m"


class ModelNotTrainedError(RuntimeError):
    """Raised instead of falling back to a heuristic when weights are missing."""


def enforce_monotonic(quantiles: np.ndarray) -> np.ndarray:
    """Non-crossing quantiles by sorting each row (Chernozhukov et al. rearrangement)."""
    return np.sort(np.asarray(quantiles, dtype=float), axis=-1)


def apply_conformal(log_ratio_quantiles: np.ndarray, offsets: Mapping[str, float]) -> np.ndarray:
    """Shift the outer pair by the 90% offset and the inner pair by the 50% offset
    (conformalized quantile regression), then re-sort so the order is still guaranteed."""
    q = enforce_monotonic(log_ratio_quantiles).copy()
    q[..., 0] -= offsets.get("interval_90", 0.0)
    q[..., 4] += offsets.get("interval_90", 0.0)
    q[..., 1] -= offsets.get("interval_50", 0.0)
    q[..., 3] += offsets.get("interval_50", 0.0)
    return enforce_monotonic(q)


class QuantileModel:
    """Five quantile boosters plus their metadata. Used for both the product model and the
    category-index model; they differ only in feature list and training data."""

    def __init__(self, boosters: Sequence[lgb.Booster], features: Sequence[str],
                 offsets: Optional[Mapping[str, float]] = None, metadata: Optional[dict] = None):
        if len(boosters) != len(QUANTILES):
            raise ValueError("expected one booster per quantile")
        self.boosters = list(boosters)
        self.features = list(features)
        self.offsets = dict(offsets or {})
        self.metadata = dict(metadata or {})

    def raw_log_ratio(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X, dtype=np.float64))
        if X.shape[1] != len(self.features):
            raise ValueError(f"expected {len(self.features)} features, got {X.shape[1]}")
        return np.column_stack([b.predict(X, num_threads=1) for b in self.boosters])

    def predict_log_ratio(self, X: np.ndarray) -> np.ndarray:
        return apply_conformal(self.raw_log_ratio(X), self.offsets)

    def save(self, directory: Path) -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        for name, booster in zip(QUANTILE_NAMES, self.boosters):
            booster.save_model(str(directory / f"{name}.lgb.txt"))
        meta = dict(self.metadata, features=self.features, conformal_offsets=self.offsets,
                    quantiles=list(QUANTILES), quantile_names=list(QUANTILE_NAMES))
        (directory / "metadata.json").write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")

    @classmethod
    def load(cls, directory: Path) -> "QuantileModel":
        directory = Path(directory)
        meta_path = directory / "metadata.json"
        if not meta_path.exists():
            raise ModelNotTrainedError(f"no trained weights in {directory}; run `python -m ml_engine.model.train`")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if lgb is None:
            raise ModelNotTrainedError("lightgbm is not installed; use ml_engine.serving (ONNX) instead")
        boosters = [lgb.Booster(model_file=str(directory / f"{name}.lgb.txt")) for name in QUANTILE_NAMES]
        return cls(boosters, meta["features"], meta.get("conformal_offsets"), meta)


# ----------------------------------------------------------------------------- decision engine

@dataclass
class Forecast:
    current_price: float
    p5: float
    p25: float
    p50: float
    p75: float
    p95: float
    projected_delta_percent: float
    action: str

    def quantiles(self) -> dict[str, float]:
        return {"p5": self.p5, "p25": self.p25, "p50": self.p50, "p75": self.p75, "p95": self.p95}


def decide(current_price: float, p25: float, p50: float, p75: float) -> str:
    """The spec's rule, evaluated in the order it is written:

    WAIT        P50 < 0.95 * P_current (expected drop > 5%)  or  P_current >= P75
    BUY NOW     P_current <= P25  or  projected rise > 5%
    FAIR PRICE  P_current inside [P25, P75]

    With non-crossing quantiles exactly one branch applies except on the P75 boundary,
    where WAIT wins because it is listed first.
    """
    if p50 < 0.95 * current_price or current_price >= p75:
        return "WAIT"
    if current_price <= p25 or p50 > 1.05 * current_price:
        return "BUY NOW"
    return "FAIR PRICE"


def forecast_from_log_ratios(current_price: float, log_ratio_quantiles: Sequence[float]) -> Forecast:
    if not np.isfinite(current_price) or current_price <= 0:
        raise ValueError("current_price must be a positive number")
    prices = enforce_monotonic(current_price * np.exp(np.asarray(log_ratio_quantiles, dtype=float)))
    prices = np.round(prices, 2)
    p5, p25, p50, p75, p95 = (float(v) for v in prices)
    delta = (p50 - current_price) / current_price * 100.0
    return Forecast(current_price=float(current_price), p5=p5, p25=p25, p50=p50, p75=p75, p95=p95,
                    projected_delta_percent=round(delta, 2), action=decide(current_price, p25, p50, p75))


class QuantileRegressor:
    """The product-level 30-day model."""

    def __init__(self, model: Optional[QuantileModel] = None, directory: Path = PRODUCT_MODEL_DIR):
        self.model = model or QuantileModel.load(directory)
        if tuple(self.model.features) != FEATURES:
            raise ModelNotTrainedError("saved weights were trained with a different feature list; retrain")

    @property
    def metadata(self) -> dict:
        return self.model.metadata

    def predict(self, feature_vector: np.ndarray, current_price: float) -> Forecast:
        log_ratios = self.model.predict_log_ratio(feature_vector)[0]
        return forecast_from_log_ratios(current_price, log_ratios)
