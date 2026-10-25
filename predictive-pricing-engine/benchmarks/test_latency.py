"""SLA latency benchmark: 20 concurrent queries, max < 40 s and p95 < 15 s.

The scraper is SIMULATED (recorded payloads + injected delay); no credits are spent. The delay
model follows what was measured against the live service:

  uncached scrape   13-47 s            cached scrape (context.dev side)   0.4-0.9 s
  plan concurrency  1 request at a time (a second concurrent call gets HTTP 429)

The same two assertions from the spec are applied to three situations. With one scrape slot
and 13-47 s per uncached scrape, 20 cold concurrent queries cannot meet p95 < 15 s: most of
them reach the 35 s internal timeout and return a structured `timeout`. That scenario is
expected to fail the p95 assertion and is left failing; the max < 40 s guarantee must hold in
all three. These tests sleep in real time (about a minute in total).
"""
from __future__ import annotations

import asyncio
import random
import time

import numpy as np
import pytest

from conftest import SimulatedContextDev, load_fixture, make_service, record_result

SLA_MAX_S, SLA_P95_S = 40.0, 15.0
N_QUERIES = 20

_TEMPLATES = (
    ("https://www.amazon.com/dp/B0{:08d}", "amazon_airpods_pro2"),
    ("https://www.bestbuy.com/site/{:07d}.p?skuId={:07d}", "bestbuy_spec_sku_6448611"),
    ("https://pcpartpicker.com/product/{:06d}/part", "pcpartpicker_7800x3d"),
    ("https://www.amazon.com/dp/B1{:08d}", "amazon_corsair_ddr5"),
)


def diverse_urls(n: int = N_QUERIES) -> dict[str, str]:
    """n distinct product URLs across retailers, each mapped to a recorded payload."""
    urls = {}
    for i in range(n):
        template, fixture = _TEMPLATES[i % len(_TEMPLATES)]
        urls[template.format(1000000 + i, 1000000 + i)] = fixture
    return urls


async def run_concurrent(service, urls) -> tuple[list[float], list[dict]]:
    async def one(url):
        t0 = time.perf_counter()
        result = await service.predict(url)
        return time.perf_counter() - t0, result

    pairs = await asyncio.gather(*(one(u) for u in urls))
    return [p[0] for p in pairs], [p[1] for p in pairs]


def summarize(name: str, latencies: list[float], results: list[dict]) -> dict:
    statuses = [r["status"] for r in results]
    summary = {"scenario": name, "queries": len(latencies), "scraper": "SIMULATED",
               "max_s": round(max(latencies), 3), "p95_s": round(float(np.percentile(latencies, 95)), 3),
               "p50_s": round(float(np.percentile(latencies, 50)), 3),
               "ok": statuses.count("ok"), "timeout": statuses.count("timeout"), "error": statuses.count("error")}
    record_result(f"latency_{name}", summary)
    print(f"\n[latency, simulated scraper] {summary}")
    return summary


def assert_no_forecast_unless_ok(results: list[dict]) -> None:
    for r in results:
        assert (r["status"] == "ok") == ("forecast" in r)
        assert r["timings_ms"]["total"] < SLA_MAX_S * 1000


async def test_sla_cold_scrapes_single_slot():
    """Worst case: nothing cached anywhere, plan concurrency 1, 13-47 s per scrape."""
    urls = diverse_urls()
    rng = random.Random(7)
    delay = {u: rng.uniform(13.0, 47.0) for u in urls}
    transport = SimulatedContextDev(latency_s=lambda u: delay[u], payload_for=lambda u: load_fixture(urls[u]))
    service = make_service(transport, gemini_latency_s=0.6)
    latencies, results = await run_concurrent(service, urls)
    await service.aclose()
    summary = summarize("cold_single_slot", latencies, results)
    assert_no_forecast_unless_ok(results)
    assert transport.rejected_429 == 0, "local serialization should keep us under the plan's concurrency limit"
    assert len(transport.calls) <= 3, "only scrapes that fit in 35 s at >= 13 s each may be sent; abandoned queued ones cost nothing"
    assert summary["max_s"] < SLA_MAX_S
    assert summary["p95_s"] < SLA_P95_S


async def test_sla_contextdev_cache_hits_single_slot():
    """Every URL is cached on context.dev's side (0.4-0.9 s each) but not in this process."""
    urls = diverse_urls()
    rng = random.Random(11)
    delay = {u: rng.uniform(0.4, 0.9) for u in urls}
    transport = SimulatedContextDev(latency_s=lambda u: delay[u], payload_for=lambda u: load_fixture(urls[u]))
    service = make_service(transport, gemini_latency_s=0.6)
    latencies, results = await run_concurrent(service, urls)
    await service.aclose()
    summary = summarize("contextdev_cached_single_slot", latencies, results)
    assert_no_forecast_unless_ok(results)
    assert summary["ok"] == N_QUERIES
    assert summary["max_s"] < SLA_MAX_S
    assert summary["p95_s"] < SLA_P95_S


async def test_sla_warm_in_process_cache():
    """Feature-cache target from the spec: under 5 s when the scrape is already cached."""
    urls = diverse_urls()
    transport = SimulatedContextDev(latency_s=lambda u: 0.0, payload_for=lambda u: load_fixture(urls[u]))
    service = make_service(transport)
    await run_concurrent(service, urls)                      # warm
    latencies, results = await run_concurrent(service, urls)  # measured
    await service.aclose()
    summary = summarize("warm_in_process_cache", latencies, results)
    assert summary["ok"] == N_QUERIES and len(transport.calls) == N_QUERIES
    assert summary["max_s"] < SLA_MAX_S
    assert summary["p95_s"] < SLA_P95_S
    assert summary["max_s"] < 5.0


async def test_internal_timeout_returns_a_structured_result_and_retry_hits_the_cache():
    """Budget scaled down (1 s budget, 2.5 s scrape) to keep the test short; same code path."""
    url = "https://www.amazon.com/dp/B0CHWRXH8B"
    transport = SimulatedContextDev(latency_s=lambda u: 2.5)
    service = make_service(transport, timeout_s=1.0)
    t0 = time.perf_counter()
    first = await service.predict(url)
    elapsed = time.perf_counter() - t0
    assert first["status"] == "timeout" and "forecast" not in first
    assert first["error"]["stage"] == "scrape" and first["error"]["scrape_in_progress"] is True
    assert first["partial"]["retailer"] == "amazon" and "macro" in first["partial"]
    assert 1.0 <= elapsed < 1.5, "must return at the budget, not hang until the scrape ends"

    await asyncio.sleep(2.0)  # the in-flight scrape finishes in the background and is cached
    second = await service.predict(url)
    await service.aclose()
    assert second["status"] == "ok" and second["timings_ms"]["total"] < 500
    assert len(transport.calls) == 1, "the retry must not pay for a second scrape"


async def test_default_internal_timeout_is_35_seconds():
    from ml_engine.config import INTERNAL_TIMEOUT_S, SLA_S
    service = make_service(SimulatedContextDev())
    assert service.timeout_s == INTERNAL_TIMEOUT_S == 35.0 and SLA_S == 40.0
    await service.aclose()


async def test_stage_budgets_gemini_cache_and_inference():
    """Gemini: cached lookups < 5 ms. Features + inference: <= 150 ms."""
    service = make_service(SimulatedContextDev(), gemini_latency_s=0.3)
    await service.gemini.macro_snapshot()
    t0 = time.perf_counter()
    for _ in range(100):
        await service.gemini.macro_snapshot()
    cached_ms = (time.perf_counter() - t0) * 1000 / 100
    assert service.gemini.network_fetches == 1 and cached_ms < 5.0

    await service.predict("https://www.amazon.com/dp/B0CHWRXH8B")  # warm-up (first LightGBM call)
    result = await service.predict("https://www.bestbuy.com/site/corsair-vengeance-32gb/6448611.p")
    await service.aclose()
    record_result("latency_stage_budgets", {"gemini_cached_ms": round(cached_ms, 4), "timings_ms": result["timings_ms"]})
    assert result["timings_ms"]["features_and_inference"] <= 150.0
