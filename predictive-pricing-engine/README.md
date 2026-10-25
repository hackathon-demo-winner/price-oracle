# PriceOracle-Intelligence: quantile price-forecasting engine

A Python pipeline that scrapes a live product price (context.dev), builds features, and runs five
LightGBM quantile models (P5 / P25 / P50 / P75 / P95 of the price 30 days ahead) to return
`WAIT`, `BUY NOW` or `FAIR PRICE`. It is meant to replace `lib/projection-engine.ts`, which
multiplies the current price by fixed constants.

## Read this first: what is real and what is not

| Piece | Status |
| --- | --- |
| Live price, title, list price | **Real.** Scraped from context.dev at request time. No price is ever invented; a failed scrape is an error. |
| Product model (`product_30d`) | Really trained LightGBM quantile models, but **trained on simulated price histories**. Its backtest numbers show the code works and is calibrated against the simulator. They are **not evidence of accuracy on real products.** |
| Category model (`category_index_1m`) | **Trained and backtested on real data** (19 BLS CPI sub-indexes, 1997-2026). It forecasts a category price index one month ahead, not an individual product. |
| Gemini prediction markets | **Real API, real client**, but it has no CPI, consumer-spending or retail contracts. The signals that exist are returned for display and are **not model inputs**. |
| Price history per product | **None yet.** In production the model runs "snapshot-only". The interface for plugging a real source in exists; see "Getting real data". |

Every service response carries `model.training_data_is_synthetic: true` and a warning string, so a
UI cannot show the forecast without the caveat being available to it.

## Layout

```
predictive-pricing-engine/
  ml_engine/
    config.py                      feature list, quantiles, 35 s / 40 s budgets
    service.py                     async orchestration, 35 s internal timeout
    model/
      train.py                     walk-forward backtest + training of both models
      quantile_regressor.py        inference, non-crossing quantiles, conformal offsets, decision rule
      export_onnx.py               ONNX export + parity check
      weights/product_30d/         5 LightGBM boosters, metadata.json, onnx/
      weights/category_index_1m/   5 LightGBM boosters, metadata.json
    data_pipeline/
      gemini_prediction.py         Gemini Prediction Markets client, 15-minute cache
      context_dev_scraper.py       context.dev client and response parser
      historical_fetcher.py        BLS category index store + pluggable price-history sources
      feature_engineering.py       feature vector, sale calendar, category classifier
      synthetic.py                 the simulator (labelled; read its docstring)
  data/bls_cpi/                    REAL monthly CPI sub-indexes (164 KB)
  benchmarks/                      tests, recorded fixtures, results/*.json from the last run
  main.py                          Firebase HTTPS function `forecastPrice` (ONNX, no scraping)
  ml_engine/serving.py             the function's handler: features -> ONNX -> response
  scripts/                         fetch_bls_cpi.py, record_fixtures.py, live_smoke.py, call_function_locally.py
```

## Running it

```bash
cd predictive-pricing-engine
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt # Windows; use .venv/bin/python elsewhere

.venv/Scripts/python -m ml_engine.model.train                # ~1 min: backtests, then writes weights
.venv/Scripts/python -m ml_engine.model.export_onnx          # optional: ONNX files + parity report
.venv/Scripts/python -m pytest benchmarks -v -s              # ~95 s, no network, no credits

# one live prediction (2 context.dev credits). The key is read from the environment only.
set CONTEXT_DEV_API_KEY=...                                  # or CONTEXT_DEV_ENV_FILE=path\to\functions\.env
.venv/Scripts/python scripts/live_smoke.py "https://www.bestbuy.com/site/6447382.p?skuId=6447382"
PRICEORACLE_LIVE=1 .venv/Scripts/python -m pytest benchmarks/test_marketplaces.py -k live
```

In code:

```python
service = PricingService()
result = await service.predict("https://www.amazon.com/dp/B0CHWRXH8B")   # never raises
```

`result["status"]` is `ok` (has `forecast`), `error` (dead page, not a product, no price, out of
stock, upstream failure) or `timeout`. Only `ok` contains a forecast.

## Data sources: what was found

### Gemini Prediction Markets (verified live, 2026-10-11)

Both endpoints exist and are public GETs.

* `/v1/prediction-markets/categories` returns 19 names, including `Economics` and `Commodities`.
* `/v1/prediction-markets/events` returns `{data, pagination{limit, offset, total}}`. Contracts carry
  `prices.bestBid`, `bestAsk`, `lastTradePrice` as strings.
* The unfiltered listing (1001 events) contains only Sports, Weather, Crypto and Commodities.
  Economics events appear only with `?category=Economics`, so the client queries per category.
* `Economics` had 10 active events: two Fed decisions, 2026 GDP growth, recession, unemployment,
  S&P / NASDAQ levels, Strait of Hormuz. `Commodities` is gold / silver / copper / oil strike ladders.
* **There is no CPI, inflation, consumer-spending, retail-sales, Prime Day or Black Friday contract.**

So the spec's `cpi_inflation_probability_next_30d` and `consumer_spending_expansion_odds` are
reported as `null` with the reason, not approximated. The client returns what does exist
(`fed_hike_probability_next_meeting`, `fed_cut_probability_next_meeting`, `recession_probability`,
`unemployment_above_5pct_probability`, `gdp_growth_above_2pct_probability`), each computed as
(bid + ask) / 2. These are not model inputs, and could not honestly be: there is no history of them
aligned with price outcomes to learn from. The two spec-named feature slots exist in the model's
input vector and are NaN in every training row, so the trees never split on them.
`event_days_to_major_sale` comes from the calendar, not from Gemini.

### context.dev (measured)

* The plan runs **one request at a time**; calls are serialized locally. Uncached scrapes took
  2-20 s in this session (13-47 s was observed earlier); cached repeats are under 1 s. 2 credits each.
* Returned fields: `name, price, regularPrice, currency, availability, category, brand, sku`.
  It does **not** return Prime status, seller (first vs third party) or Best Buy model number, so
  `is_first_party_seller` is always unknown (NaN) and is not guessed.
* Checked URLs: Amazon `B0CHWRXH8B` and `B0C3RYHZJQ` work. The spec's Amazon ASIN `B082DH1FWM` is a
  404 (returned as `not_found`). The spec's Best Buy SKU `6448611` works ($249.99, Corsair Vengeance
  LPX DDR4). PCPartPicker product pages work, and the vendor table gives lowest vendor and price
  spread. A Newegg page returned a product with `price: null` (returned as `no_price`). B&H was not tested.

### Historical prices: the crux

| Source | Finding |
| --- | --- |
| Keepa | Paid API. Not used. The fastest way to get years of real Amazon history. |
| CamelCamelCamel | No public API. Its public product page, read through context.dev (1 credit, 1.8 s), does expose **all-time low, high and average** as text, e.g. AirPods Pro 2: low $159.99, high $299.00, average $224.37. That is a summary, not a series, so it cannot train a model, but it can feed three features at inference. Implemented as the opt-in `CamelSummarySource`; check their terms before enabling it. |
| PCPartPicker | The price-history chart is drawn client-side; the scrape returns only axis labels, no data points. Bulk scraping is against their terms. Not used. |
| Hugging Face | Searched; no product-level retail price time series found (stock, crypto, trading-card and static Amazon listing datasets only). |
| Billion Prices Project (Harvard Dataverse) | The public files are aggregate indexes. Micro price data exists (2007-2015, mostly supermarkets) behind a data-use agreement, which was not accepted on the user's behalf. |
| **BLS CPI sub-indexes** | **Used.** Public domain, no key. 19 category series, 344-355 months each (1997-2026), 6,361 usable training rows. Category level only. |

Conclusion: no free, licensed, product-level daily price series was obtainable. The product model
is therefore trained on a **semi-synthetic** generator (`data_pipeline/synthetic.py`): each
simulated product follows the real BLS index of its category over real calendar dates and the real
sale calendar, while everything product-specific (how often it is discounted, how deep, for how
long, shortage spikes, repricing noise) uses invented parameters. 1,500 products, 125,959 rows.

### Getting real data (cheapest path first)

1. **Log what the cron already scrapes (free).** `functions/src/tracker-cron.ts` scrapes a live
   price for every active alert every 4 hours and discards it. Writing it down builds a first-party
   dataset at zero extra context.dev cost:

   ```
   price_history/{autoId}
     retailer: "amazon" | "bestbuy"     productId: string (ASIN / SKU)
     price: number                      regularPrice: number | null
     availability: string | null        currency: string
     observedAt: serverTimestamp        source: "context.dev"
   ```

   Write one document per successful scrape (skip when `price` is missing), scrape each distinct
   product once per run even if several alerts watch it, and also log the price from user-initiated
   lookups. Add a composite index on `(retailer, productId, observedAt)`. A scheduled export to
   `data/price_logs/<retailer>/<productId>.csv` (`date,price`) is exactly what `CsvPriceLogSource`
   already reads, so those products get real history features with no further code. Limits: the
   cron reads at most 50 alerts per retailer, labels need 30 days to mature, and a useful training
   set needs a few months and more products than are tracked today. Not implemented here: the
   functions code is outside this change.
2. **Keepa API (paid, roughly EUR 49/month at the entry tier; confirm the current price).** Gives
   years of daily Amazon history immediately. Implement it as another `PriceHistorySource`, then
   retrain with `train.py` on real series and re-run the same backtest.
3. **Camel summary (1 credit per lookup).** Inference-time enrichment only.

When real series exist, replace `load_synthetic_dataset` in `train.py` with a loader that returns
the same columns; nothing else changes, and `training_data_is_synthetic` flips to false.

## The model

* Target: `log(price in 30 days / price now)`, so one model serves any price level.
* Five LightGBM boosters, `objective=quantile`, alpha in {0.05, 0.25, 0.50, 0.75, 0.95}.
* Monotonicity: the five outputs are sorted (rearrangement), then conformal offsets fitted on a
  held-out, later slice of the training window widen or narrow the outer and inner intervals
  (conformalized quantile regression), then sorted again.
* 23 features (`config.FEATURES`): the spec's momentum features, snapshot features (price vs list
  price), retailer, category, calendar, the real BLS category trend, and the three inert slots.
* History is masked on 55% of training rows (40% snapshot-only, 15% summary-only) because that is
  what production looks like today. With no history, those features are NaN, not defaults.
* Decision rule, exactly as in the spec and in its order: `WAIT` if P50 < 0.95 x current or current
  >= P75; else `BUY NOW` if current <= P25 or P50 > 1.05 x current; else `FAIR PRICE`.

## Benchmark results (run of 2026-10-11, `pytest benchmarks`: 98 passed, 2 failed, 1 skipped)

Raw numbers are in `benchmarks/results/*.json`. Thresholds are the spec's and were not adjusted.

### Product model: SYNTHETIC data (shows the pipeline works; not real-world accuracy)

Out-of-time walk-forward, expanding window; each fold is tested on the following period.

| Test period | n | Pinball loss (mean of 5) | Naive baseline | P5-P95 coverage | P25-P75 coverage | MAE | RMSE |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2022 | 11,929 | 0.01888 | 0.02873 | 0.902 | 0.534 | 4.87% / $8.14 | 10.89% / $28.30 |
| 2023 | 11,109 | 0.02007 | 0.03024 | 0.904 | 0.483 | 5.30% / $7.99 | 12.21% / $26.27 |
| 2024 | 10,434 | 0.01789 | 0.02743 | 0.903 | 0.509 | 4.65% / $6.72 | 10.48% / $21.71 |
| 2025-01 to 2026-07 (hold-out of the shipped weights) | 8,005 | 0.01702 | 0.02436 | 0.888 | 0.511 | 4.33% / $8.78 | 9.50% / $36.43 |

MAE / RMSE are of P50 against the simulated future price, as % of the current price and in dollars.
A "price will not change" forecast has MAE 6.10% on the hold-out. By history mode on the hold-out:
full history 0.884 coverage / 4.22% MAE, summary-only 0.897 / 4.02%, snapshot-only 0.889 / 4.56%.
Coverage >= 0.88 passes in all four folds; the last is close to the line (0.888).

### Category index model: REAL BLS data (category level, one month ahead)

| Test period | n | Pinball loss | Naive baseline | P5-P95 coverage | MAE | RMSE |
| --- | --- | --- | --- | --- | --- | --- |
| 2010-2014 | 1,140 | 0.00203 | 0.00249 | 0.908 | 0.63% | 0.84% |
| 2015-2019 | 1,140 | 0.00268 | 0.00313 | **0.871 (fails >= 0.88)** | 0.81% | 1.15% |
| 2020-2022 | 684 | 0.00325 | 0.00355 | 0.898 | 1.02% | 1.36% |
| 2023-2026 (hold-out of the shipped weights) | 779 | 0.00317 | 0.00360 | 0.895 | 0.98% | 1.40% |

It beats the unconditional-quantile baseline on pinball loss in every fold, but the median is only
marginally better than "no change" (2020-2022: 1.02% vs 1.02%). Category indexes are close to a
random walk at one month; the value here is the interval, not the point forecast.

### Latency: SIMULATED scraper (recorded payloads + injected delay), 20 concurrent queries

| Scenario | max | p95 | Outcome | max < 40 s | p95 < 15 s |
| --- | --- | --- | --- | --- | --- |
| Cold: 13-47 s per scrape, one slot | 35.0 s | 35.0 s | 1 ok, 19 timeout | pass | **fails** |
| Cached at context.dev (0.4-0.9 s each), one slot | 13.6 s | 12.9 s | 20 ok | pass | pass |
| Warm in-process cache | 0.014 s | 0.013 s | 20 ok | pass | pass |

Other measurements: Gemini cached lookup 0.0002 ms (budget 5 ms); feature extraction plus inference
about 1-3 ms (budget 150 ms); one live end-to-end call on a context.dev cache hit took 0.66 s
(scrape 0.44 s, Gemini 0.66 s in parallel).

### The two failing assertions, left failing

1. **p95 < 15 s for 20 cold concurrent queries.** Not achievable on this plan: one scrape at a time
   at 13-47 s each means at most two or three scrapes fit inside 35 s. The service does what it
   can: every caller gets an answer by 35.0 s (max < 40 s holds), 19 of 20 get a structured
   `timeout`, queued scrapes that nobody is waiting for are never sent (no credits spent), and the
   scrape in flight finishes in the background so a retry hits the cache. Fixing the p95 needs a
   context.dev plan with more concurrency, and even then an uncached scrape alone exceeds 15 s.
2. **P5-P95 coverage >= 0.88 on real category data, 2015-2019 fold: 0.871.** The other three real
   folds pass (0.895-0.908).

## Deployed function (`main.py`, Firebase Functions 2nd gen, Python 3.12)

`main.py` exports one HTTPS function, `forecastPrice` (us-central1, 512 MB, 30 s timeout). It does
**not** scrape and never calls context.dev: the Node functions scrape the product and post the
price. It serves the ONNX weights with `onnxruntime` + `numpy` only (`requirements.txt`);
LightGBM, pandas and aiohttp are research dependencies (`requirements-dev.txt`) and are not deployed.

Request (POST, JSON):

```json
{"currentPrice": 249.69, "regularPrice": null, "category": null, "retailer": "bestbuy",
 "title": "Apple - AirPods Pro 2", "productId": "6447382"}
```

Response:

```json
{"success": true, "forecast": {
  "currentPrice": 249.69,
  "quantiles": {"lowest": 206.43, "low": 245.99, "mid": 249.69, "high": 250.54, "highest": 297.81},
  "projectedMid": 249.69, "deltaPercentMonth": 0.0, "recommendation": "FAIR PRICE",
  "subtitle": "Within the model's typical 30-day range ($245.99 to $250.54)",
  "confidence": null,
  "model": {"name": "product_30d", "version": "2026-10-11-945d3da7", "trainedOn": "synthetic",
            "trainedAt": "2026-10-11T22:10:04Z", "horizonDays": 30, "historyMode": "snapshot_only",
            "category": "audio", "retailer": "bestbuy"},
  "macro": {"source": "Gemini Prediction Markets", "signals": {"recession_probability": 0.035, "...": "..."},
            "fetchedAt": "...", "usedByModel": false}}}
```

* Non-POST: 405 `{"success": false, "error": {"code": "method_not_allowed", ...}}`.
* Missing, non-numeric or non-positive `currentPrice`, or a non-object body: 400.
* `confidence` is always `null`. The model has not been validated on real prices, so there is no
  honest basis for a number.
* `macro` is the Gemini display block from a 15-minute in-memory cache. On a cold instance the first
  request waits up to 1.5 s for Gemini; if Gemini fails or is slow, `macro` is `null`, the forecast is
  still returned, and the lookup is retried at most once a minute. It never affects the forecast.
* `productId` is accepted and unused today; it is the key a price-history lookup will use.

### What the model sees when there is no price history (every product today)

Of the 23 inputs, the function supplies 11 and leaves 12 as missing (NaN). Nothing is imputed with
a default value; LightGBM/ONNX trees route a missing value down a learned "missing" branch, and 40%
of training rows had history masked the same way.

| Supplied | Source |
| --- | --- |
| `log_price` | `currentPrice` |
| `price_ratio_to_regular` | `currentPrice / regularPrice`; missing when `regularPrice` is null or below the price |
| `retailer_encoded` | `retailer` (amazon, bestbuy, newegg, pcpartpicker, bhphoto; anything else is "other") |
| `product_category_encoded` | keyword match on `category` + `title`; "other" when nothing matches |
| `event_days_to_major_sale`, `in_sale_window_now`, `target_in_sale_window`, `month` | today's date (UTC) and the sale calendar |
| `category_cpi_3m_change`, `category_cpi_12m_change` | real BLS index of the category, as last published; missing for "other" |

Missing: all ten price-history features, `is_first_party_seller`, and the two Gemini slots.

What that produces, measured over a grid of 2,560 no-history requests per row (10 categories x 4
retailers x 16 dates x 4 prices):

| `regularPrice` | Median forecast vs current price | Recommendation | P5 / P95 around the current price |
| --- | --- | --- | --- |
| null | 0.0% in 84% of cases (range -3.1% to +0.1%) | FAIR PRICE 95.7%, WAIT 4.1%, BUY NOW 0.2% | P5 from -12% to -55% (median -18%); P95 from +2% to +45% (median +7%) |
| equal to the price | +0.4% (range -1.7% to +0.5%) | FAIR PRICE 89%, BUY NOW 11% | similar |
| 10% above the price | +9.5% | **BUY NOW 100%** | P5 about -7%, P95 about +17% |
| 25% above the price | +24.6% | **BUY NOW 100%** | P5 about -2%, P95 about +28% |

In plain terms:

* **Without a list price, the forecast is "the price stays where it is", with an interval whose
  width depends on category, retailer and how close a sale event is.** The recommendation is almost
  always FAIR PRICE. It is a distribution, not a constant, but the point forecast carries no
  product-specific information, because there is none to use.
* **With a list price above the current price, the model predicts a return to the list price and
  says BUY NOW every time.** That is the simulator's rule (a shown list price is the true
  undiscounted price and promotions end within weeks). Real retailers often show a permanently
  inflated strikethrough price, so on real products this will over-recommend BUY NOW for anything
  displaying one. If that is not acceptable, send `regularPrice: null` from the Node side until the
  model is retrained on real data.

### Local testing and measurements

```bash
python -m venv venv && venv/Scripts/python -m pip install -r requirements.txt   # the slim deploy env
venv/Scripts/python scripts/call_function_locally.py     # 3 examples, 400, 405, timings (live Gemini)
.venv/Scripts/python -m pytest benchmarks/test_function.py
```

Measured on this Windows machine with only `requirements.txt` installed: imports about 0.6-0.7 s,
model load about 0.16 s, warm request median 0.26 ms / p95 0.37 ms through the function wrapper.
The first request on a new instance also waits for Gemini (0.8 s here, capped at 1.5 s). Estimated
cold start once deployed: roughly 2-4 s (instance start plus about 1 s of imports and model load on a
slower shared CPU, plus the Gemini lookup). That is an estimate, not a measurement.

### Deploying (not done here)

Uploaded source with the ignore list below: 43 files, 2.7 MB (five ONNX files are 2.45 MB of it).
Installed dependencies are about 195 MB (onnxruntime 48 MB, numpy 34 MB, the rest is
firebase-functions' own dependencies).

```json
{
  "source": "predictive-pricing-engine",
  "codebase": "ml",
  "runtime": "python312",
  "ignore": [
    "venv", ".venv", ".cache", ".scratch", ".pytest_cache", "**/__pycache__",
    "benchmarks", "scripts", "data/price_logs",
    "**/*.lgb.txt", "ml_engine/model/weights/category_index_1m",
    "requirements-dev.txt", "pytest.ini", "README.md", ".gitignore", ".gitattributes",
    ".git", "firebase-debug.log", "firebase-debug.*.log"
  ]
}
```

Things to know before deploying:

* The Firebase CLI needs a virtualenv named `venv` inside the source directory, with
  `requirements.txt` installed, to discover the function (`python -m venv venv`, then pip install).
  It is git-ignored and in the ignore list.
* The function is created publicly invocable. It only computes, but anyone with the URL can call
  it; restrict the invoker or add a shared secret if that matters.
* The BLS files in `data/bls_cpi` are a snapshot (latest month: August 2026). After about 200 days
  without a refresh the two category-trend inputs go missing and the forecast falls back to the
  remaining inputs. Re-run `scripts/fetch_bls_cpi.py` and redeploy every few months.
* Prime Day, Prime Big Deal Days and back-to-school use typical dates, not announced ones.
* `data/bls_cpi` and `ml_engine/` must not be added to the ignore list; the function reads both.

## Integration with the site (nothing was deployed)

The site is a static export and the backend is Node Cloud Functions, so this Python service cannot
run there as-is. Options:

| Option | What it takes | Added latency | Notes |
| --- | --- | --- | --- |
| **ONNX inside the existing functions** | `onnxruntime-node`, the five `.onnx` files (2.5 MB, already exported), and a TypeScript port of `feature_engineering.py` plus the sort / conformal / decision step (about 200 lines) | A few ms per prediction once loaded; loading the runtime adds to cold start (not measured) | No new infrastructure. Risk is train/serve skew in the port; export golden feature vectors from Python and test the port against them. |
| Cloud Run Python service called from the functions | Containerize this package, add auth between function and service | One extra network hop when warm; a multi-second cold start unless a minimum instance is kept warm (which costs money). Neither was measured. | One code path for features. The natural home once history features need Firestore reads and retraining is routine. |
| Batch scoring | A scheduled job scores tracked products and writes forecasts to Firestore | None at request time | Fits the alerts cron and the email content. Does not cover ad-hoc URL lookups. |

Recommendation: ONNX in the existing functions for ad-hoc lookups, because the scrape (seconds)
dominates and the model adds milliseconds; add batch scoring for tracked alerts once `price_history`
exists. Move to Cloud Run only if keeping the TypeScript feature port in sync becomes a burden.
ONNX parity with LightGBM: maximum difference 1.8e-7 on four of the five models over 5,000 rows; on
P95, 2 of 5,000 rows differ by up to 1.4e-4 in log-ratio (float32 inputs landing on a split
threshold), which is about 0.01% of the price. Whatever hosts it, the caller must handle `timeout`
with a retry, since a cold scrape routinely exceeds the budget.

## Not done

* No validation on real product prices. Until `price_history` or Keepa data exists, treat the
  product forecast as experimental and do not show it as a confident prediction.
* Only the 30-day distribution is modelled, not a day-by-day trajectory.
* Gemini signals are displayed, not used by the model.
* Prime status, seller and model number are not available from context.dev.
* The TypeScript port for the ONNX path is not written. B&H was not tested live.
* The simulator's parameters are assumptions; a different simulator would give a different model.

Spec note: the requirements header mentions coverage for P10-P90, while the benchmark section
specifies [P5, P95] >= 88%. The latter is what the models produce and what is tested.
