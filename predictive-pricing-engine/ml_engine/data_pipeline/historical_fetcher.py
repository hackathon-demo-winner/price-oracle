"""Historical price data, behind interfaces so a real source can be plugged in.

What is real today
------------------
* `CategoryIndexStore`  - REAL. Monthly BLS CPI-U sub-indexes per product category
  (data/bls_cpi, downloaded by scripts/fetch_bls_cpi.py). Category level, not product level.
* `CsvPriceLogSource`   - reads first-party price logs (date,price CSV per product). This is
  the format a Firestore `price_history` export would produce. No logs exist yet.
* `CamelSummarySource`  - opt-in. Reads the all-time low / high / average that
  camelcamelcamel prints on its public product page, through context.dev. Real numbers, but
  a summary, not a series; costs one extra scrape and is subject to that site's terms.
* `NullHistorySource`   - the default. Returns nothing, so the model runs snapshot-only.

There is deliberately no source here that fabricates a history for a product.
"""
from __future__ import annotations

import asyncio
import bisect
import csv
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Protocol, Sequence

import numpy as np

from ..config import BLS_DIR, HISTORY_BUDGET_S, PROJECT_ROOT
from .feature_engineering import HistorySummary, PriceHistory

NAN = float("nan")
CPI_PUBLICATION_LAG_DAYS = 45  # month M is published around the middle of month M+1


# ----------------------------------------------------------------------------- category index (real)

class CategoryIndexStore:
    """Real monthly category price indexes from BLS."""

    def __init__(self, directory: Path = BLS_DIR):
        self.directory = Path(directory)
        manifest_path = self.directory / "series.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"{manifest_path} is missing; run scripts/fetch_bls_cpi.py")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.source: str = manifest["source"]
        self.series_meta: dict[str, dict] = manifest["series"]
        self._dates: dict[str, list[date]] = {}
        self._values: dict[str, np.ndarray] = {}
        self._interpolated: dict[str, set[date]] = {}
        for category, meta in self.series_meta.items():
            dates, values = [], []
            with (self.directory / f"{meta['series_id']}.csv").open(encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    dates.append(date.fromisoformat(row["date"]))
                    values.append(float(row["value"]))
            dates, values, filled = self._fill_single_month_gaps(dates, values)
            self._dates[category] = dates
            self._values[category] = np.asarray(values)
            self._interpolated[category] = filled

    @staticmethod
    def _fill_single_month_gaps(dates: list[date], values: list[float]) -> tuple[list[date], list[float], set[date]]:
        """BLS did not collect October 2025. A single missing month is filled with the geometric
        mean of its neighbours so that trailing-change features can span it. Filled months are
        remembered: they are never used as a prediction target or as a reference month."""
        out_d, out_v, filled = [dates[0]], [values[0]], set()
        for d, v in zip(dates[1:], values[1:]):
            prev = out_d[-1]
            if (d.year * 12 + d.month) - (prev.year * 12 + prev.month) == 2:
                mid = date(prev.year + (prev.month == 12), prev.month % 12 + 1, 1)
                out_d.append(mid)
                out_v.append(float(np.sqrt(out_v[-1] * v)))
                filled.add(mid)
            out_d.append(d)
            out_v.append(v)
        return out_d, out_v, filled

    def is_interpolated(self, category: str, month: date) -> bool:
        return month in self._interpolated.get(category, set())

    @property
    def categories(self) -> list[str]:
        return list(self._dates)

    def monthly(self, category: str) -> tuple[list[date], np.ndarray]:
        return self._dates[category], self._values[category]

    def _published_index(self, category: str, as_of: date) -> int:
        """Index of the latest observation that had been published by `as_of`."""
        cutoff = as_of - timedelta(days=CPI_PUBLICATION_LAG_DAYS)
        return bisect.bisect_right(self._dates[category], cutoff.replace(day=1)) - 1

    def trend(self, category: str, as_of: date) -> tuple[float, float]:
        """(3-month, 12-month) fractional change of the category index as known at `as_of`."""
        if category not in self._dates:
            return NAN, NAN
        i = self._published_index(category, as_of)
        dates, values = self._dates[category], self._values[category]
        if i < 0 or (as_of - dates[i]).days > 200:  # nothing recent enough was published
            return NAN, NAN

        def change(months: int) -> float:
            target = (dates[i].year * 12 + dates[i].month - 1) - months
            j = bisect.bisect_left(dates, date(target // 12, target % 12 + 1, 1))
            if j >= len(dates) or j >= i or (dates[j].year * 12 + dates[j].month - 1) != target:
                return NAN  # BLS skipped that month
            return float(values[i] / values[j] - 1.0)

        return change(3), change(12)

    @staticmethod
    def index_features(values: np.ndarray, i: int) -> Optional[dict]:
        """Features of the category-index model at month index i (needs 13 earlier months)."""
        if i < 13:
            return None
        logs = np.log(values[i - 13: i + 1])
        changes = np.diff(logs)
        return {"change_1m": float(changes[-1]), "change_3m": float(logs[-1] - logs[-4]),
                "change_12m": float(logs[-1] - logs[-13]), "volatility_12m": float(changes[-12:].std())}

    def latest_index_features(self, category: str, as_of: date) -> Optional[dict]:
        """Category-index model inputs for the latest month published by `as_of`, or None when
        the category has no index or the last 14 months are not all present."""
        if category not in self._dates:
            return None
        i = self._published_index(category, as_of)
        dates = self._dates[category]
        if i < 13 or (as_of - dates[i]).days > 200 or self.is_interpolated(category, dates[i]):
            return None
        span = (dates[i].year * 12 + dates[i].month) - (dates[i - 13].year * 12 + dates[i - 13].month)
        if span != 13:
            return None
        feats = self.index_features(self._values[category], i)
        feats.update({"month": float(dates[i].month), "category_encoded": float(self.categories.index(category)),
                      "reference_month": dates[i].isoformat()[:7]})
        return feats

    def daily_level(self, category: str, days: Sequence[date]) -> np.ndarray:
        """Index level linearly interpolated to days (used only by the synthetic generator)."""
        dates, values = self._dates[category], self._values[category]
        x = np.array([d.toordinal() for d in dates], dtype=float)
        return np.interp(np.array([d.toordinal() for d in days], dtype=float), x, values)


# ----------------------------------------------------------------------------- product history sources

@dataclass
class HistoryResult:
    history: Optional[PriceHistory] = None
    summary: Optional[HistorySummary] = None
    source: str = "none"
    note: str = ""

    @property
    def mode(self) -> str:
        if self.history is not None and len(self.history) >= 8:
            return "full_history"
        if self.summary is not None:
            return "summary_only"
        return "snapshot_only"


class PriceHistorySource(Protocol):
    name: str

    async def fetch(self, retailer: str, product_id: Optional[str], url: str, as_of: date) -> Optional[HistoryResult]:
        """Return real history for the product, or None when this source has none."""


class NullHistorySource:
    name = "none"

    async def fetch(self, retailer: str, product_id: Optional[str], url: str, as_of: date) -> Optional[HistoryResult]:
        return None


class CsvPriceLogSource:
    """First-party price logs: <root>/<retailer>/<product_id>.csv with header `date,price`.

    Several observations per day are reduced to the last one; gaps are forward-filled so the
    series is daily. Only observations strictly before `as_of` are used.
    """
    name = "first_party_price_log"

    def __init__(self, root: Path = PROJECT_ROOT / "data" / "price_logs", max_gap_days: int = 14):
        self.root = Path(root)
        self.max_gap_days = max_gap_days

    async def fetch(self, retailer: str, product_id: Optional[str], url: str, as_of: date) -> Optional[HistoryResult]:
        if not product_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", product_id):
            return None
        path = self.root / retailer / f"{product_id}.csv"
        if not path.exists():
            return None
        by_day: dict[date, float] = {}
        with path.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    day = datetime.fromisoformat(row["date"].replace("Z", "+00:00")).date()
                    price = float(row["price"])
                except (KeyError, ValueError):
                    continue
                if price > 0 and day < as_of:
                    by_day[day] = price
        if len(by_day) < 8:
            return None
        days = sorted(by_day)
        # Keep only the most recent run without a gap longer than max_gap_days.
        start = 0
        for i in range(1, len(days)):
            if (days[i] - days[i - 1]).days > self.max_gap_days:
                start = i
        days = days[start:]
        if len(days) < 8 or (as_of - days[-1]).days > self.max_gap_days:
            return None
        series, cursor, last = [], days[0], by_day[days[0]]
        while cursor < as_of:
            last = by_day.get(cursor, last)
            series.append(last)
            cursor += timedelta(days=1)
        return HistoryResult(history=PriceHistory(prices=series, source=self.name), source=self.name)


_CAMEL_ROW = re.compile(r"^\|\s*(?:\xa0|\s)*(Amazon|3rd Party New)\s*\|(.+)$", re.M)
_MONEY = re.compile(r"\$([0-9][0-9,]*\.?[0-9]*)")


def parse_camel_summary(markdown: str) -> Optional[HistorySummary]:
    """Parse the 'Lowest Ever | Highest Ever | Current | Average' table of a camelcamelcamel
    product page (as markdown). Prefers the Amazon row, falls back to 3rd Party New."""
    rows: dict[str, HistorySummary] = {}
    for match in _CAMEL_ROW.finditer(markdown or ""):
        cells = [c.strip() for c in match.group(2).split("|")]
        if len(cells) < 4:
            continue

        def money(cell: str) -> Optional[float]:
            m = _MONEY.search(cell)
            return float(m.group(1).replace(",", "")) if m else None

        low, high, avg = money(cells[0]), money(cells[1]), money(cells[3])
        if low and high and avg and low <= avg <= high:
            rows[match.group(1)] = HistorySummary(low, high, avg, source=f"camelcamelcamel:{match.group(1)}")
    return rows.get("Amazon") or rows.get("3rd Party New")


class CamelSummarySource:
    """Opt-in: all-time low/high/average for an Amazon ASIN from camelcamelcamel's public page,
    read through context.dev (1 credit). The scrape shares context.dev's single request slot,
    so it runs after the product scrape and can exceed the 2 s history budget on a cache miss,
    in which case the service simply continues without it."""
    name = "camelcamelcamel_summary"

    def __init__(self, scraper):
        self._scraper = scraper

    async def fetch(self, retailer: str, product_id: Optional[str], url: str, as_of: date) -> Optional[HistoryResult]:
        if retailer != "amazon" or not product_id:
            return None
        markdown = await self._scraper.scrape_markdown(f"https://camelcamelcamel.com/product/{product_id}")
        summary = parse_camel_summary(markdown or "")
        if summary is None:
            return None
        return HistoryResult(summary=summary, source=self.name)


class HistoricalFetcher:
    """Tries each source in order inside a time budget and returns the first real result."""

    def __init__(self, sources: Optional[Sequence[PriceHistorySource]] = None, budget_s: float = HISTORY_BUDGET_S):
        self.sources = list(sources) if sources is not None else [CsvPriceLogSource()]
        self.budget_s = budget_s

    async def fetch(self, retailer: str, product_id: Optional[str], url: str, as_of: date) -> HistoryResult:
        async def run() -> HistoryResult:
            for source in self.sources:
                result = await source.fetch(retailer, product_id, url, as_of)
                if result is not None:
                    return result
            return HistoryResult(note="no price history source has data for this product")

        try:
            return await asyncio.wait_for(run(), timeout=self.budget_s)
        except asyncio.TimeoutError:
            return HistoryResult(note=f"price history lookup exceeded its {self.budget_s:.1f}s budget")
        except Exception as err:  # a history source must never take the prediction down
            return HistoryResult(note=f"price history lookup failed: {type(err).__name__}")
