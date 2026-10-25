"""The deployable function (main.forecastPrice), called directly with fake requests.

No network: the Gemini lookup is replaced by the recorded Economics response (or a failure).
"""
from __future__ import annotations

import json
import time
from datetime import date

import pytest
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from conftest import FIXTURES, record_result
from ml_engine import serving
from ml_engine.serving import MacroCache, handle_forecast

TODAY = date(2026, 10, 11)
EXAMPLES = {
    "bestbuy_audio_249_69": {"currentPrice": 249.69, "regularPrice": None, "category": None, "retailer": "bestbuy",
                             "title": "Apple - AirPods Pro 2, Wireless Active Noise Cancelling Earbuds - White", "productId": "6447382"},
    "amazon_ram_609_99": {"currentPrice": 609.99, "regularPrice": None, "category": "Computer Memory", "retailer": "amazon",
                          "title": "CORSAIR Vengeance DDR5 RAM 64GB (2x32GB) 6000MHz CL30", "productId": "B0C3RYHZJQ"},
    "beauty_14_99": {"currentPrice": 14.99, "regularPrice": None, "category": "Beauty", "retailer": "amazon",
                     "title": "Maybelline Lash Sensational Mascara", "productId": None},
}


def recorded_gemini(timeout_s: float) -> list[dict]:
    return json.loads((FIXTURES / "gemini" / "events_Economics.json").read_text(encoding="utf-8"))["data"]


@pytest.fixture(autouse=True)
def offline_macro(monkeypatch):
    monkeypatch.setattr(serving, "_macro", MacroCache(fetch=recorded_gemini))


def call_function(method: str = "POST", body=None, raw: bytes | None = None):
    """Invoke the decorated Firebase function exactly as the runtime does: with a WSGI request."""
    import main
    kwargs = {"method": method, "path": "/"}
    if raw is not None:
        kwargs.update(data=raw, content_type="application/json")
    elif body is not None:
        kwargs["json"] = body
    response = main.forecastPrice(Request(EnvironBuilder(**kwargs).get_environ()))
    return response.status_code, json.loads(response.get_data(as_text=True)), response


@pytest.mark.parametrize("name", list(EXAMPLES))
def test_example_products_return_the_contract(name):
    status, payload = handle_forecast("POST", EXAMPLES[name], today=TODAY)
    record_result(f"function_{name}", {"request": EXAMPLES[name], "as_of": TODAY, "response": payload})
    assert status == 200 and payload["success"] is True
    forecast = payload["forecast"]
    q = forecast["quantiles"]
    values = [q["lowest"], q["low"], q["mid"], q["high"], q["highest"]]
    assert values == sorted(values) and all(v > 0 and round(v, 2) == v for v in values)
    assert q["lowest"] < q["highest"], "a no-history forecast must still be a distribution, not a point"
    assert forecast["projectedMid"] == q["mid"]
    current = EXAMPLES[name]["currentPrice"]
    assert forecast["deltaPercentMonth"] == pytest.approx((q["mid"] - current) / current * 100, abs=0.05)
    assert round(forecast["deltaPercentMonth"], 1) == forecast["deltaPercentMonth"]
    assert forecast["recommendation"] in ("BUY NOW", "WAIT", "FAIR PRICE")
    assert isinstance(forecast["subtitle"], str) and 10 < len(forecast["subtitle"]) < 90
    assert forecast["confidence"] is None
    assert forecast["model"]["trainedOn"] == "synthetic" and forecast["model"]["name"] == "product_30d"
    assert forecast["model"]["version"] and forecast["model"]["trainedAt"]
    assert forecast["macro"]["signals"]["recession_probability"] == pytest.approx(0.035)
    assert set(forecast) >= {"quantiles", "projectedMid", "deltaPercentMonth", "recommendation", "subtitle", "confidence", "model", "macro"}


def test_recommendation_follows_the_spec_rule():
    from ml_engine.model.quantile_regressor import decide
    for body in EXAMPLES.values():
        f = handle_forecast("POST", body, today=TODAY)[1]["forecast"]
        assert f["recommendation"] == decide(body["currentPrice"], f["quantiles"]["low"], f["quantiles"]["mid"], f["quantiles"]["high"])


def test_outputs_are_not_a_single_constant_shape():
    """Category, retailer and list price must move the distribution, not just rescale it."""
    def shape(body):
        f = handle_forecast("POST", body, today=TODAY)[1]["forecast"]
        return tuple(round(f["quantiles"][k] / body["currentPrice"], 3) for k in ("lowest", "low", "mid", "high", "highest"))

    shapes = {shape(b) for b in EXAMPLES.values()}
    shapes.add(shape({**EXAMPLES["bestbuy_audio_249_69"], "regularPrice": 299.99}))
    assert len(shapes) == 4


def test_function_wrapper_returns_json_over_http_semantics():
    status, payload, response = call_function("POST", EXAMPLES["bestbuy_audio_249_69"])
    assert status == 200 and payload["success"] is True
    assert response.headers["Content-Type"] == "application/json"


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_non_post_is_405(method):
    status, payload, response = call_function(method)
    assert status == 405 and payload == {"success": False, "error": {"code": "method_not_allowed", "message": "Use POST with a JSON body."}}
    assert response.headers["Allow"] == "POST"


@pytest.mark.parametrize("body", [
    {}, {"currentPrice": None}, {"currentPrice": 0}, {"currentPrice": -5}, {"currentPrice": "249.69"},
    {"currentPrice": True}, {"currentPrice": float("inf")}, {"retailer": "amazon"},
])
def test_missing_or_non_positive_price_is_400(body):
    status, payload = handle_forecast("POST", body)
    assert status == 400 and payload["success"] is False and payload["error"]["code"] == "invalid_current_price"
    assert "forecast" not in payload


def test_malformed_bodies_are_400():
    assert call_function("POST", raw=b"{not json")[0] == 400
    assert call_function("POST", raw=b"[1, 2]")[0] == 400
    assert handle_forecast("POST", {"currentPrice": 10, "regularPrice": -1})[0] == 400
    assert handle_forecast("POST", {"currentPrice": 10, "title": 5})[0] == 400


def test_unknown_retailer_and_category_still_forecast():
    status, payload = handle_forecast("POST", {"currentPrice": 42.0, "retailer": "Some Shop", "title": None, "category": None}, today=TODAY)
    assert status == 200 and payload["forecast"]["model"]["retailer"] == "other" and payload["forecast"]["model"]["category"] == "other"


def test_answers_when_gemini_is_down_and_does_not_retry_every_request(monkeypatch):
    def down(timeout_s):
        raise ConnectionError("gemini unreachable")

    cache = MacroCache(fetch=down)
    monkeypatch.setattr(serving, "_macro", cache)
    for _ in range(5):
        status, payload = handle_forecast("POST", EXAMPLES["beauty_14_99"], today=TODAY)
        assert status == 200 and payload["forecast"]["macro"] is None
    assert cache.network_fetches == 1


def test_slow_gemini_is_cut_off_at_its_budget(monkeypatch):
    def slow(timeout_s):
        time.sleep(1.0)
        return []

    monkeypatch.setattr(serving, "_macro", MacroCache(fetch=slow, budget_s=0.1))
    t0 = time.perf_counter()
    status, payload = handle_forecast("POST", EXAMPLES["beauty_14_99"], today=TODAY)
    assert status == 200 and payload["forecast"]["macro"] is None and time.perf_counter() - t0 < 0.6


def test_macro_cache_is_15_minutes():
    now = [0.0]
    cache = MacroCache(fetch=recorded_gemini, clock=lambda: now[0])
    cache.get(); cache.get()
    now[0] = 14 * 60
    cache.get()
    assert cache.network_fetches == 1
    now[0] = 16 * 60
    cache.get()
    assert cache.network_fetches == 2


class _NoMacro:
    def get(self):
        return None



def test_onnx_serving_matches_the_lightgbm_service_path():
    """Same inputs through the research path (LightGBM) and the deployed path (ONNX)."""
    pytest.importorskip("lightgbm")
    import numpy as np
    from ml_engine.config import FEATURES
    from ml_engine.data_pipeline.feature_engineering import FeatureInputs, build_features
    from ml_engine.model.quantile_regressor import QuantileRegressor

    model, cpi = serving.load()
    regressor = QuantileRegressor()
    for body, category in ((EXAMPLES["bestbuy_audio_249_69"], "audio"), (EXAMPLES["amazon_ram_609_99"], "computer_hardware")):
        cpi_3m, cpi_12m = cpi.trend(category, TODAY)
        feats = build_features(FeatureInputs(current_price=body["currentPrice"], as_of=TODAY, retailer=body["retailer"],
                                             category=category, category_cpi_3m_change=cpi_3m, category_cpi_12m_change=cpi_12m))
        vector = np.array([feats[n] for n in FEATURES])
        expected = regressor.predict(vector, body["currentPrice"]).quantiles()
        # no macro signals: this test compares the two model runtimes, not the Gemini heuristic
        got = handle_forecast("POST", body, today=TODAY, macro=_NoMacro())[1]["forecast"]["quantiles"]
        for a, b in zip(expected.values(), got.values()):
            assert a == pytest.approx(b, abs=0.02)


def test_warm_latency():
    for _ in range(20):
        handle_forecast("POST", EXAMPLES["bestbuy_audio_249_69"], today=TODAY)
    t0 = time.perf_counter()
    n = 300
    for _ in range(n):
        call_function("POST", EXAMPLES["bestbuy_audio_249_69"])
    per_call_ms = (time.perf_counter() - t0) * 1000 / n
    record_result("function_warm_latency", {"calls": n, "mean_ms_per_request_through_wrapper": round(per_call_ms, 3)})
    assert per_call_ms < 50
