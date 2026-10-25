"""Unit tests for the pieces: decision rule, Gemini client, parsers, features, history sources."""
from __future__ import annotations

import json
import math
from datetime import date, timedelta

import numpy as np
import pytest

from conftest import FIXTURES, SimulatedContextDev, gemini_fixture_fetch, load_fixture, make_service
from ml_engine.config import FEATURES, INERT_FEATURES
from ml_engine.data_pipeline.context_dev_scraper import (ContextDevScraper, ScrapeError, parse_product_response,
                                                        product_id_from_url)
from ml_engine.data_pipeline.feature_engineering import (FeatureInputs, HistorySummary, PriceHistory, build_features,
                                                        classify_category, days_to_next_major_sale, major_sale_dates)
from ml_engine.data_pipeline.gemini_prediction import (GeminiPredictionClient, build_macro_signals, implied_probability)
from ml_engine.data_pipeline.historical_fetcher import (CamelSummarySource, CategoryIndexStore, CsvPriceLogSource,
                                                       HistoricalFetcher, parse_camel_summary)
from ml_engine.model.quantile_regressor import apply_conformal, decide, forecast_from_log_ratios


# ----------------------------------------------------------------------------- decision rule (spec section 4)

@pytest.mark.parametrize("current,p25,p50,p75,expected", [
    (100, 85, 94.9, 110, "WAIT"),        # P50 < 0.95 * current
    (100, 80, 96, 100, "WAIT"),          # current >= P75 (boundary)
    (100, 80, 96, 99, "WAIT"),           # current above P75
    (100, 100, 101, 110, "BUY NOW"),     # current <= P25 (boundary)
    (100, 102, 104, 110, "BUY NOW"),     # current below P25
    (100, 99, 105.1, 110, "BUY NOW"),    # projected rise > 5%
    (100, 99, 105, 110, "FAIR PRICE"),   # rise of exactly 5% is not "> 5%"
    (100, 97, 95, 103, "FAIR PRICE"),    # drop of exactly 5% is not "> 5%"
    (100, 95, 100, 105, "FAIR PRICE"),
])
def test_decision_rule(current, p25, p50, p75, expected):
    assert decide(current, p25, p50, p75) == expected


def test_forecast_sorts_crossing_quantiles_and_computes_delta():
    forecast = forecast_from_log_ratios(200.0, [0.05, -0.2, -0.1, 0.0, -0.3])
    values = list(forecast.quantiles().values())
    assert values == sorted(values)
    assert forecast.projected_delta_percent == pytest.approx((forecast.p50 - 200) / 200 * 100, abs=0.01)
    with pytest.raises(ValueError):
        forecast_from_log_ratios(0.0, [0, 0, 0, 0, 0])


def test_conformal_offsets_keep_order():
    q = apply_conformal(np.array([[-0.01, -0.005, 0.0, 0.005, 0.01]]), {"interval_90": -0.05, "interval_50": 0.02})
    assert np.all(np.diff(q, axis=1) >= 0)


# ----------------------------------------------------------------------------- Gemini

def test_implied_probability_is_the_bid_ask_midpoint():
    assert implied_probability({"bestBid": "0.82", "bestAsk": "0.86"}) == pytest.approx(0.84)
    assert implied_probability({"bestBid": None, "bestAsk": "0.03", "lastTradePrice": "0.02"}) == pytest.approx(0.02)
    assert implied_probability({"bestBid": None, "bestAsk": None}) is None
    assert implied_probability(None) is None


def test_macro_signals_from_the_recorded_live_response():
    events = json.loads((FIXTURES / "gemini" / "events_Economics.json").read_text(encoding="utf-8"))["data"]
    signals = build_macro_signals(events)
    assert signals["cpi_inflation_probability_next_30d"] is None       # no such contract exists
    assert signals["consumer_spending_expansion_odds"] is None
    for name in ("fed_hike_probability_next_meeting", "recession_probability",
                 "unemployment_above_5pct_probability", "gdp_growth_above_2pct_probability"):
        assert 0.0 <= signals[name] <= 1.0, name
    assert signals["recession_probability"] == pytest.approx(0.035)    # (0.03 + 0.04) / 2 in the recording


async def test_gemini_cache_lasts_15_minutes_and_failure_is_explicit():
    now = [1000.0]
    client = GeminiPredictionClient(fetch_json=gemini_fixture_fetch(), clock=lambda: now[0])
    first = await client.macro_snapshot()
    await client.macro_snapshot()
    assert client.network_fetches == 1 and first.status == "live"
    now[0] += 14 * 60
    await client.macro_snapshot()
    assert client.network_fetches == 1
    now[0] += 2 * 60
    await client.macro_snapshot()
    assert client.network_fetches == 2
    assert "Economics" in await client.list_categories()

    async def broken(url, params):
        raise ConnectionError("down")

    snapshot = await GeminiPredictionClient(fetch_json=broken).macro_snapshot()
    assert snapshot.status == "unavailable" and snapshot.signals == {} and snapshot.model_features() == {}


async def test_gemini_budget_is_enforced():
    client = GeminiPredictionClient(fetch_json=gemini_fixture_fetch(latency_s=0.5), budget_s=0.1)
    snapshot = await client.macro_snapshot()
    assert snapshot.status == "unavailable" and "timed out" in snapshot.note


# ----------------------------------------------------------------------------- context.dev parsing

def test_parse_recorded_products():
    amazon = parse_product_response(load_fixture("amazon_airpods_pro2"), "https://www.amazon.com/dp/B0CHWRXH8B")
    assert (amazon.retailer, amazon.product_id, amazon.price, amazon.in_stock) == ("amazon", "B0CHWRXH8B", 263.86, True)
    bestbuy = parse_product_response(load_fixture("bestbuy_spec_sku_6448611"),
                                     "https://www.bestbuy.com/site/corsair-vengeance-32gb/6448611.p")
    assert (bestbuy.retailer, bestbuy.product_id, bestbuy.price) == ("bestbuy", "6448611", 249.99)
    assert bestbuy.is_first_party_seller is None  # not exposed by context.dev; never guessed


@pytest.mark.parametrize("fixture,url,code", [
    ("amazon_dead_spec_asin", "https://www.amazon.com/dp/B082DH1FWM", "not_found"),
    ("bestbuy_home_not_a_product", "https://www.bestbuy.com/", "not_a_product_page"),
    ("newegg_7800x3d", "https://www.newegg.com/p/N82E16819113793", "no_price"),
])
def test_parse_failures_raise(fixture, url, code):
    with pytest.raises(ScrapeError) as err:
        parse_product_response(load_fixture(fixture), url)
    assert err.value.code == code


def test_product_ids():
    assert product_id_from_url("https://www.amazon.com/Some-Name/dp/b0chwrxh8b/ref=x") == "B0CHWRXH8B"
    assert product_id_from_url("https://www.bestbuy.com/site/6447382.p?skuId=6447382") == "6447382"
    assert product_id_from_url("https://www.newegg.com/p/N82E16819113793") == "N82E16819113793"
    assert product_id_from_url("https://example.com/item/1") is None


async def test_scraper_retries_429_then_succeeds(monkeypatch):
    calls = []

    async def transport(body, headers, timeout_s):
        calls.append(body["url"])
        if len(calls) == 1:
            return 429, {"Retry-After": "0.5"}, {}
        return 200, {}, load_fixture("amazon_airpods_pro2")

    scraper = ContextDevScraper(api_key="test-key-not-real", transport=transport)
    product = await scraper.scrape_product("https://www.amazon.com/dp/B0CHWRXH8B")
    assert product.price == 263.86 and len(calls) == 2 and scraper.live_calls == 2


# ----------------------------------------------------------------------------- features

def test_no_history_means_missing_features_not_invented_ones():
    feats = build_features(FeatureInputs(current_price=100.0, as_of=date(2026, 10, 11), retailer="amazon", category="audio"))
    assert list(feats) == list(FEATURES)
    for name in ("price_ratio_to_30d_avg", "price_ratio_to_90d_high", "price_ratio_to_all_time_low",
                 "rolling_7d_volatility", "rolling_30d_volatility", "days_since_last_price_change",
                 "frequency_of_discounts_per_quarter", "price_ratio_to_regular", *INERT_FEATURES):
        assert math.isnan(feats[name]), name
    assert feats["log_price"] == pytest.approx(math.log(100))


def test_history_features_on_a_known_series():
    prices = [100.0] * 100 + [80.0] * 5 + [100.0] * 20   # one 20% promotion, then 20 flat days
    feats = build_features(FeatureInputs(current_price=100.0, as_of=date(2026, 6, 1), history=PriceHistory(prices)))
    assert feats["price_ratio_to_90d_high"] == pytest.approx(1.0)
    assert feats["price_ratio_to_all_time_low"] == pytest.approx(1.25)
    assert feats["days_since_last_price_change"] == pytest.approx(20)
    assert feats["frequency_of_discounts_per_quarter"] == pytest.approx(90 / 91)
    assert feats["rolling_7d_volatility"] == 0.0 and feats["rolling_30d_volatility"] > 0


def test_summary_fills_only_what_it_supports():
    feats = build_features(FeatureInputs(current_price=263.86, as_of=date(2026, 10, 11),
                                         summary=HistorySummary(159.99, 299.0, 224.37)))
    assert feats["price_ratio_to_all_time_low"] == pytest.approx(263.86 / 159.99)
    assert feats["price_ratio_to_all_time_avg"] == pytest.approx(263.86 / 224.37)
    assert math.isnan(feats["rolling_30d_volatility"])


def test_sale_calendar():
    assert date(2026, 11, 27) in major_sale_dates(2026)          # Black Friday 2026
    assert date(2026, 11, 30) in major_sale_dates(2026)          # Cyber Monday 2026
    assert days_to_next_major_sale(date(2026, 11, 20)) == 7
    assert days_to_next_major_sale(date(2026, 12, 15)) > 200     # rolls into next year


def test_category_classifier():
    assert classify_category("CORSAIR Vengeance DDR5 RAM 32GB") == "computer_hardware"
    assert classify_category("Apple AirPods Pro 2 Wireless Earbuds") == "audio"
    assert classify_category("Mystery object") == "other"


# ----------------------------------------------------------------------------- history sources

def test_camel_summary_parser_on_the_recorded_page():
    markdown = load_fixture("camel_price_history_page")["markdown"]["data"]
    summary = parse_camel_summary(markdown)
    assert (summary.all_time_low, summary.all_time_high, summary.all_time_avg) == (159.99, 299.0, 224.37)
    assert parse_camel_summary("no table here") is None


async def test_camel_summary_source_changes_history_mode():
    transport = SimulatedContextDev()
    service = make_service(transport)
    service.history = HistoricalFetcher([CamelSummarySource(service.scraper)])
    result = await service.predict("https://www.amazon.com/dp/B0CHWRXH8B")
    await service.aclose()
    assert result["status"] == "ok" and result["model"]["history_mode"] == "summary_only"
    assert result["features"]["price_ratio_to_all_time_low"] == pytest.approx(263.86 / 159.99, abs=1e-5)


async def test_csv_price_log_source(tmp_path):
    as_of = date(2026, 10, 11)
    folder = tmp_path / "amazon"
    folder.mkdir()
    rows = ["date,price"] + [f"{(as_of - timedelta(days=d)).isoformat()}T08:00:00Z,{100 + (d % 3)}" for d in range(40, 0, -2)]
    (folder / "B0TEST00001.csv").write_text("\n".join(rows), encoding="utf-8")
    source = CsvPriceLogSource(root=tmp_path)
    result = await source.fetch("amazon", "B0TEST00001", "", as_of)
    assert result.mode == "full_history" and len(result.history) == 40   # daily, forward-filled
    assert await source.fetch("amazon", "MISSING", "", as_of) is None
    assert await source.fetch("amazon", "../etc/passwd", "", as_of) is None


async def test_history_budget_and_failure_never_break_a_prediction():
    import asyncio

    class Slow:
        name = "slow"

        async def fetch(self, *args):
            await asyncio.sleep(1.0)

    class Broken:
        name = "broken"

        async def fetch(self, *args):
            raise RuntimeError("boom")

    slow = await HistoricalFetcher([Slow()], budget_s=0.05).fetch("amazon", "X", "", date(2026, 1, 1))
    broken = await HistoricalFetcher([Broken()]).fetch("amazon", "X", "", date(2026, 1, 1))
    assert slow.mode == broken.mode == "snapshot_only" and "budget" in slow.note and "failed" in broken.note


# ----------------------------------------------------------------------------- real category data

def test_bls_store_is_real_and_bridges_only_the_unpublished_month():
    cpi = CategoryIndexStore()
    assert len(cpi.categories) == 19 and "Bureau of Labor Statistics" in cpi.source
    assert cpi.is_interpolated("computer_hardware", date(2025, 10, 1))
    assert not cpi.is_interpolated("computer_hardware", date(2025, 9, 1))
    three, twelve = cpi.trend("computer_hardware", date(2026, 10, 11))
    assert math.isfinite(three) and math.isfinite(twelve)
    assert cpi.trend("other", date(2026, 10, 11)) == (pytest.approx(float("nan"), nan_ok=True),) * 2


def test_onnx_export_matches_lightgbm():
    ort = pytest.importorskip("onnxruntime")
    from ml_engine.model.quantile_regressor import PRODUCT_MODEL_DIR, QuantileModel
    onnx_dir = PRODUCT_MODEL_DIR / "onnx"
    if not (onnx_dir / "p50.onnx").exists():
        pytest.skip("run python -m ml_engine.model.export_onnx first")
    model = QuantileModel.load(PRODUCT_MODEL_DIR)
    rng = np.random.default_rng(0)
    X = rng.normal(1.0, 0.5, size=(500, len(FEATURES))).astype(np.float32)
    X[rng.random(X.shape) < 0.4] = np.nan
    for i, name in enumerate(("p5", "p25", "p50", "p75", "p95")):
        session = ort.InferenceSession(str(onnx_dir / f"{name}.onnx"), providers=["CPUExecutionProvider"])
        onnx_pred = session.run(None, {"input": X})[0].reshape(-1)
        lgb_pred = model.boosters[i].predict(X.astype(np.float64))
        assert np.max(np.abs(onnx_pred - lgb_pred)) < 1e-4, name
