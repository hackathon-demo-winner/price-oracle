"""Async orchestration: scrape -> (Gemini, history) -> features -> quantile model -> decision.

    python -m ml_engine.service https://www.amazon.com/dp/B0CHWRXH8B

`PricingService.predict` never raises and always returns within the 35 s internal budget:

  {"status": "ok", ...forecast...}      a real scraped price went through the trained model
  {"status": "error", "error": {...}}   the product could not be read (dead page, no price, ...)
  {"status": "timeout", "error": {...}} the budget ran out; `partial` holds what was gathered

An error or timeout response never contains a forecast or a price that was not scraped.

Why timeouts are expected: an uncached context.dev scrape takes up to ~47 s and the plan runs
one scrape at a time. A scrape that has already been sent is left running after the caller
times out, so it lands in the cache and a retry answers in well under a second. A scrape that
is still waiting for the slot when its last caller gives up is cancelled and costs nothing.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

import numpy as np

from .config import FEATURES, HORIZON_DAYS, INTERNAL_TIMEOUT_S, QUANTILE_NAMES
from .data_pipeline.context_dev_scraper import (ContextDevScraper, ProductSnapshot, ScrapeError,
                                                product_id_from_url, validate_url)
from .data_pipeline.feature_engineering import (FeatureInputs, build_features, classify_category, retailer_from_url)
from .data_pipeline.gemini_prediction import GeminiPredictionClient, MacroSnapshot
from .data_pipeline.historical_fetcher import CategoryIndexStore, HistoricalFetcher, HistoryResult
from .model.quantile_regressor import (CATEGORY_MODEL_DIR, ModelNotTrainedError, QuantileModel, QuantileRegressor)


@dataclass
class _ScrapeJob:
    task: asyncio.Task
    waiters: int = 0
    started: bool = False


class PricingService:
    def __init__(self, scraper: Optional[ContextDevScraper] = None, gemini: Optional[GeminiPredictionClient] = None,
                 history: Optional[HistoricalFetcher] = None, regressor: Optional[QuantileRegressor] = None,
                 cpi: Optional[CategoryIndexStore] = None, category_model: Optional[QuantileModel] = None,
                 timeout_s: float = INTERNAL_TIMEOUT_S, today: Callable[[], date] = date.today):
        self.scraper = scraper or ContextDevScraper()
        self.gemini = gemini or GeminiPredictionClient()
        self.history = history or HistoricalFetcher()
        self.regressor = regressor or QuantileRegressor()
        self.cpi = cpi or CategoryIndexStore()
        if category_model is None:
            try:
                category_model = QuantileModel.load(CATEGORY_MODEL_DIR)
            except ModelNotTrainedError:
                category_model = None
        self.category_model = category_model
        self.timeout_s = timeout_s
        self._today = today
        self._jobs: dict[str, _ScrapeJob] = {}

    # ------------------------------------------------------------------ shared scrapes

    def _job_for(self, url: str) -> _ScrapeJob:
        job = self._jobs.get(url)
        if job is None:
            def on_start() -> None:
                job.started = True

            def on_done(task: asyncio.Task) -> None:
                if self._jobs.get(url) is job:
                    del self._jobs[url]
                if not task.cancelled():
                    task.exception()  # mark as retrieved; callers get it through their own await

            job = _ScrapeJob(task=asyncio.ensure_future(self.scraper.scrape_product(url, on_start=on_start)))
            job.task.add_done_callback(on_done)
            self._jobs[url] = job
        return job

    async def _scrape(self, url: str, ctx: dict) -> ProductSnapshot:
        job = self._job_for(url)
        job.waiters += 1
        ctx["scrape_job"] = job
        try:
            return await asyncio.shield(job.task)
        finally:
            job.waiters -= 1
            if job.waiters == 0 and not job.started and not job.task.done():
                job.task.cancel()  # never sent: do not spend credits on an abandoned request

    async def aclose(self) -> None:
        jobs = list(self._jobs.values())
        for job in jobs:
            job.task.cancel()
        await asyncio.gather(*(job.task for job in jobs), return_exceptions=True)

    # ------------------------------------------------------------------ pipeline

    async def predict(self, url: str) -> dict:
        started = time.perf_counter()
        ctx: dict = {"stage": "validate", "timings_ms": {}, "url": url}
        macro_task: Optional[asyncio.Task] = None
        try:
            clean_url = validate_url(url)
            ctx.update(url=clean_url, retailer=retailer_from_url(clean_url))
            ctx["product_id"] = product_id_from_url(clean_url, ctx["retailer"])
            macro_task = asyncio.ensure_future(self._timed(self.gemini.macro_snapshot(), ctx, "gemini"))
            result = await asyncio.wait_for(self._pipeline(clean_url, ctx, macro_task), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            result = self._timeout_response(ctx, macro_task)
        except ScrapeError as err:
            result = self._error_response(ctx, err.code, err.message, err.retryable)
        except ModelNotTrainedError as err:
            result = self._error_response(ctx, "model_not_trained", str(err), False)
        except Exception as err:  # last resort: a structured error, never a guessed forecast
            result = self._error_response(ctx, "internal_error", f"{type(err).__name__}: {err}", False)
        finally:
            if macro_task is not None and not macro_task.done():
                macro_task.cancel()
        ctx["timings_ms"]["total"] = round((time.perf_counter() - started) * 1000, 1)
        result["timings_ms"] = ctx["timings_ms"]
        return result

    async def _timed(self, awaitable, ctx: dict, name: str):
        t0 = time.perf_counter()
        try:
            return await awaitable
        finally:
            ctx["timings_ms"][name] = round((time.perf_counter() - t0) * 1000, 1)

    async def _pipeline(self, url: str, ctx: dict, macro_task: asyncio.Task) -> dict:
        ctx["stage"] = "scrape"
        product: ProductSnapshot = await self._timed(self._scrape(url, ctx), ctx, "scrape")
        ctx["product"] = product

        ctx["stage"] = "enrich"
        as_of = self._today()
        history: HistoryResult = await self._timed(
            self.history.fetch(product.retailer, product.product_id, url, as_of), ctx, "history")
        macro: MacroSnapshot = await macro_task

        ctx["stage"] = "inference"
        t0 = time.perf_counter()
        category = classify_category(product.name, product.category)
        cpi_3m, cpi_12m = self.cpi.trend(category, as_of)
        features = build_features(FeatureInputs(
            current_price=product.price, as_of=as_of, retailer=product.retailer, category=category,
            regular_price=product.regular_price, is_first_party_seller=product.is_first_party_seller,
            history=history.history, summary=history.summary,
            category_cpi_3m_change=cpi_3m, category_cpi_12m_change=cpi_12m, macro=macro.model_features(),
        ))
        vector = np.array([features[name] for name in FEATURES], dtype=np.float64)
        forecast = self.regressor.predict(vector, product.price)
        category_trend = self._category_trend(category, as_of)
        ctx["timings_ms"]["features_and_inference"] = round((time.perf_counter() - t0) * 1000, 2)

        meta = self.regressor.metadata
        warnings = []
        if meta.get("training_data_is_synthetic"):
            warnings.append("The model was trained on simulated price histories. Treat the forecast as experimental.")
        if history.mode == "snapshot_only":
            warnings.append("No price history is available for this product; the forecast uses only the current "
                            "price, list price, retailer, category and calendar.")
        if product.in_stock is False:
            warnings.append("The retailer lists this product as out of stock; the scraped price may be stale.")
        if product.currency and product.currency != "USD":
            warnings.append(f"Price is in {product.currency}; the model was built for USD retail prices.")

        return {
            "status": "ok",
            "url": url,
            "retailer": product.retailer,
            "product_id": product.product_id,
            "product": {**product.to_json(), "model_category": category},
            "forecast": {
                "horizon_days": HORIZON_DAYS,
                "current_price": forecast.current_price,
                "quantiles": forecast.quantiles(),
                "projected_delta_percent": forecast.projected_delta_percent,
                "action": forecast.action,
            },
            "model": {
                "name": meta.get("model"),
                "training_data": meta.get("training_data"),
                "training_data_is_synthetic": bool(meta.get("training_data_is_synthetic")),
                "trained_at": meta.get("trained_at"),
                "history_mode": history.mode,
                "history_source": history.source,
                "history_note": history.note,
            },
            "category_trend": category_trend,
            "macro": macro.to_json(),
            "features": {k: (None if v != v else round(v, 6)) for k, v in features.items()},
            "warnings": warnings,
        }

    def _category_trend(self, category: str, as_of: date) -> Optional[dict]:
        """One-month category price-index forecast from the model trained on REAL BLS data."""
        if self.category_model is None:
            return None
        feats = self.cpi.latest_index_features(category, as_of)
        if feats is None:
            return None
        row = np.array([[feats[name] for name in self.category_model.features]], dtype=np.float64)
        pct = (np.exp(self.category_model.predict_log_ratio(row)[0]) - 1.0) * 100.0
        return {
            "category": category,
            "bls_series": self.cpi.series_meta[category]["series_id"],
            "reference_month": feats["reference_month"],
            "next_month_index_change_percent": {name: round(float(v), 3) for name, v in zip(QUANTILE_NAMES, pct)},
            "last_12m_change_percent": round((np.exp(feats["change_12m"]) - 1) * 100, 2),
            "training_data": self.category_model.metadata.get("training_data"),
            "note": "Category-level index, not this product's price.",
        }

    # ------------------------------------------------------------------ failures

    def _partial(self, ctx: dict, macro_task: Optional[asyncio.Task] = None) -> dict:
        partial = {"retailer": ctx.get("retailer"), "product_id": ctx.get("product_id")}
        product = ctx.get("product")
        if product is not None:
            partial["product"] = product.to_json()
        if macro_task is not None and macro_task.done() and not macro_task.cancelled() and macro_task.exception() is None:
            partial["macro"] = macro_task.result().to_json()
        return partial

    def _error_response(self, ctx: dict, code: str, message: str, retryable: bool) -> dict:
        return {"status": "error", "url": ctx.get("url"),
                "error": {"code": code, "message": message, "stage": ctx.get("stage"), "retryable": retryable},
                "partial": self._partial(ctx)}

    def _timeout_response(self, ctx: dict, macro_task: Optional[asyncio.Task]) -> dict:
        job: Optional[_ScrapeJob] = ctx.get("scrape_job")
        in_flight = bool(job and job.started and not job.task.done())
        if ctx.get("stage") == "scrape":
            message = (f"The product page was not read within {self.timeout_s:.0f}s. "
                       + ("The scrape is still running and will be cached; retry in a few seconds."
                          if in_flight else "The scraper was busy with other requests; retry shortly."))
        else:
            message = f"The pipeline did not finish within {self.timeout_s:.0f}s (stage: {ctx.get('stage')})."
        return {"status": "timeout", "url": ctx.get("url"),
                "error": {"code": "timeout", "message": message, "stage": ctx.get("stage"), "retryable": True,
                          "scrape_in_progress": in_flight},
                "partial": self._partial(ctx, macro_task)}


async def _main(url: str) -> None:
    service = PricingService()
    try:
        print(json.dumps(await service.predict(url), indent=2))
    finally:
        await service.aclose()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m ml_engine.service <product-url>")
    asyncio.run(_main(sys.argv[1]))
