"""End-to-end marketplace tests: recorded context.dev responses -> full service -> JSON.

The scrape payloads are real (recorded 2026-10-11); only the network hop is replayed.
A live smoke test runs when PRICEORACLE_LIVE=1 (it spends 2 context.dev credits per URL).
"""
from __future__ import annotations

import json
import math
import os

import pytest

from conftest import SimulatedContextDev, load_fixture, make_service, record_result

AMAZON = "https://www.amazon.com/dp/B0CHWRXH8B"
AMAZON_SPEC_DEAD = "https://www.amazon.com/dp/B082DH1FWM"            # the spec's example ASIN: a 404
BESTBUY_SPEC = "https://www.bestbuy.com/site/corsair-vengeance-32gb/6448611.p"  # the spec's example SKU
PCPARTPICKER = "https://pcpartpicker.com/product/3hyH99/amd-ryzen-7-7800x3d-42-ghz-8-core-processor-100-100000910wof"
NEWEGG_NO_PRICE = "https://www.newegg.com/p/N82E16819113793"
NOT_A_PRODUCT = "https://www.bestbuy.com/"

QUANTILES = ("p5", "p25", "p50", "p75", "p95")


def assert_valid_forecast(result: dict, expected_price: float) -> None:
    json.loads(json.dumps(result))  # serializable, no NaN objects
    assert result["status"] == "ok", result
    forecast = result["forecast"]
    values = [forecast["quantiles"][q] for q in QUANTILES]
    assert all(isinstance(v, float) and math.isfinite(v) and v > 0 for v in values)
    assert values == sorted(values), "quantiles must be non-decreasing"
    assert forecast["current_price"] == pytest.approx(expected_price)
    assert forecast["action"] in ("WAIT", "BUY NOW", "FAIR PRICE")
    expected_delta = (forecast["quantiles"]["p50"] - expected_price) / expected_price * 100
    assert forecast["projected_delta_percent"] == pytest.approx(expected_delta, abs=0.01)
    assert result["model"]["training_data_is_synthetic"] is True
    assert any("simulated" in w for w in result["warnings"]), "synthetic training must be disclosed in the response"


@pytest.mark.parametrize("url,retailer,product_id,price", [
    (AMAZON, "amazon", "B0CHWRXH8B", 263.86),
    (BESTBUY_SPEC, "bestbuy", "6448611", 249.99),
    (PCPARTPICKER, "pcpartpicker", "3hyH99", 348.0),
])
async def test_marketplace_returns_full_forecast(url, retailer, product_id, price):
    service = make_service(SimulatedContextDev())
    result = await service.predict(url)
    await service.aclose()
    assert_valid_forecast(result, price)
    assert result["retailer"] == retailer and result["product_id"] == product_id
    assert result["macro"]["status"] == "live" and result["macro"]["used_as_model_input"] is False
    record_result(f"marketplace_{retailer}", result)


async def test_pcpartpicker_extracts_lowest_vendor_and_spread():
    service = make_service(SimulatedContextDev())
    result = await service.predict(PCPARTPICKER)
    await service.aclose()
    market = result["product"]["marketplace"]
    assert market["lowest_price"] == 348.0 and market["vendors"] >= 2
    assert market["price_spread"] == pytest.approx(market["highest_price"] - market["lowest_price"])
    assert result["product"]["model_category"] == "computer_hardware"


@pytest.mark.parametrize("url,code", [
    (AMAZON_SPEC_DEAD, "not_found"),
    (NEWEGG_NO_PRICE, "no_price"),
    (NOT_A_PRODUCT, "not_a_product_page"),
    ("not a url", "invalid_url"),
])
async def test_unreadable_product_is_an_explicit_error_without_a_forecast(url, code):
    service = make_service(SimulatedContextDev())
    result = await service.predict(url)
    await service.aclose()
    assert result["status"] == "error" and result["error"]["code"] == code
    assert "forecast" not in result
    assert "price" not in json.dumps(result["partial"])


async def test_out_of_stock_without_price_is_an_error():
    def payload(url):  # derived from a real recording: stock and price removed
        data = load_fixture("bestbuy_spec_sku_6448611")
        data["product"]["data"]["product"].update(availability="out_of_stock", price=None)
        return data

    service = make_service(SimulatedContextDev(payload_for=payload))
    result = await service.predict(BESTBUY_SPEC)
    await service.aclose()
    assert result["status"] == "error" and result["error"]["code"] == "out_of_stock"
    assert "forecast" not in result


async def test_out_of_stock_with_a_price_forecasts_with_a_warning():
    def payload(url):
        data = load_fixture("bestbuy_spec_sku_6448611")
        data["product"]["data"]["product"]["availability"] = "out_of_stock"
        return data

    service = make_service(SimulatedContextDev(payload_for=payload))
    result = await service.predict(BESTBUY_SPEC)
    await service.aclose()
    assert_valid_forecast(result, 249.99)
    assert any("out of stock" in w for w in result["warnings"])


async def test_upstream_failure_is_an_error_not_a_guess():
    class Failing(SimulatedContextDev):
        async def __call__(self, body, headers, timeout_s):
            return 500, {}, {"error": "boom"}

    service = make_service(Failing())
    result = await service.predict(AMAZON)
    await service.aclose()
    assert result["status"] == "error" and result["error"]["code"] == "upstream_error"
    assert result["error"]["retryable"] is True and "forecast" not in result


async def test_missing_api_key_is_reported():
    from ml_engine.data_pipeline.context_dev_scraper import ContextDevScraper
    service = make_service(SimulatedContextDev())
    service.scraper = ContextDevScraper(api_key="", transport=SimulatedContextDev())
    result = await service.predict(AMAZON)
    await service.aclose()
    assert result["error"]["code"] == "missing_api_key"


@pytest.mark.skipif(os.environ.get("PRICEORACLE_LIVE") != "1", reason="live smoke test; set PRICEORACLE_LIVE=1 (spends credits)")
async def test_live_smoke():
    from ml_engine.service import PricingService
    service = PricingService()
    try:
        result = await service.predict(os.environ.get("PRICEORACLE_LIVE_URL", AMAZON))
    finally:
        await service.aclose()
    assert result["status"] in ("ok", "timeout"), result
    assert result["timings_ms"]["total"] < 40_000
    if result["status"] == "ok":
        assert_valid_forecast(result, result["forecast"]["current_price"])
