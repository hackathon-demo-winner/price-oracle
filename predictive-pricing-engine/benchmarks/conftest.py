"""Shared test helpers: recorded fixtures and a simulated context.dev.

Nothing here touches the network. The context.dev payloads are real responses recorded by
scripts/record_fixtures.py; the Gemini payloads are real responses saved on 2026-10-11.
"""
from __future__ import annotations

import asyncio
import copy
import json
import sys
from datetime import date
from pathlib import Path
from typing import Callable, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_engine.data_pipeline.context_dev_scraper import ContextDevScraper  # noqa: E402
from ml_engine.data_pipeline.gemini_prediction import GeminiPredictionClient  # noqa: E402
from ml_engine.data_pipeline.historical_fetcher import HistoricalFetcher, NullHistorySource  # noqa: E402
from ml_engine.service import PricingService  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
RESULTS = Path(__file__).resolve().parent / "results"
FIXED_TODAY = date(2026, 10, 11)

# URL -> recorded fixture name
RECORDED = {
    "https://www.amazon.com/dp/B0CHWRXH8B": "amazon_airpods_pro2",
    "https://www.amazon.com/dp/B0C3RYHZJQ": "amazon_corsair_ddr5",
    "https://www.amazon.com/dp/B082DH1FWM": "amazon_dead_spec_asin",
    "https://www.bestbuy.com/site/6447382.p?skuId=6447382": "bestbuy_airpods_pro2",
    "https://www.bestbuy.com/site/corsair-vengeance-32gb/6448611.p": "bestbuy_spec_sku_6448611",
    "https://pcpartpicker.com/product/3hyH99/amd-ryzen-7-7800x3d-42-ghz-8-core-processor-100-100000910wof": "pcpartpicker_7800x3d",
    "https://www.bestbuy.com/": "bestbuy_home_not_a_product",
    "https://www.newegg.com/p/N82E16819113793": "newegg_7800x3d",
    "https://camelcamelcamel.com/product/B0CHWRXH8B": "camel_price_history_page",
}


def load_fixture(name: str) -> dict:
    return copy.deepcopy(json.loads((FIXTURES / "contextdev" / f"{name}.json").read_text(encoding="utf-8"))["response"])


class SimulatedContextDev:
    """Stands in for the context.dev HTTP endpoint. Replays recorded payloads after an injected
    delay and, like the real plan, answers 429 if a second request arrives while one is running."""

    def __init__(self, latency_s: Callable[[str], float] = lambda url: 0.0,
                 payload_for: Optional[Callable[[str], dict]] = None):
        self.latency_s = latency_s
        self.payload_for = payload_for or (lambda url: load_fixture(RECORDED[url]))
        self.active = 0
        self.calls: list[str] = []
        self.rejected_429 = 0

    async def __call__(self, body: dict, headers: dict, timeout_s: float):
        assert headers["Authorization"].startswith("Bearer "), "the scraper must send the bearer key"
        if self.active >= 1:
            self.rejected_429 += 1
            return 429, {"Retry-After": "1"}, {}
        self.active += 1
        try:
            self.calls.append(body["url"])
            await asyncio.sleep(self.latency_s(body["url"]))
            return 200, {}, self.payload_for(body["url"])
        finally:
            self.active -= 1


def gemini_fixture_fetch(latency_s: float = 0.0):
    async def fetch(url: str, params: dict) -> dict:
        await asyncio.sleep(latency_s)
        if url.endswith("/categories"):
            return json.loads((FIXTURES / "gemini" / "categories.json").read_text(encoding="utf-8"))
        return json.loads((FIXTURES / "gemini" / f"events_{params['category']}.json").read_text(encoding="utf-8"))
    return fetch


def make_service(transport: SimulatedContextDev, timeout_s: float = 35.0, gemini_latency_s: float = 0.0,
                 history: Optional[HistoricalFetcher] = None) -> PricingService:
    scraper = ContextDevScraper(api_key="test-key-not-real", transport=transport)
    return PricingService(
        scraper=scraper,
        gemini=GeminiPredictionClient(fetch_json=gemini_fixture_fetch(gemini_latency_s)),
        history=history or HistoricalFetcher([NullHistorySource()]),
        timeout_s=timeout_s,
        today=lambda: FIXED_TODAY,
    )


def record_result(name: str, payload: dict) -> None:
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"{name}.json").write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")


@pytest.fixture
def simulated():
    return SimulatedContextDev()
