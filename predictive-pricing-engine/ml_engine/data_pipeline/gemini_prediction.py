"""Gemini Prediction Markets client.

Verified against the live API on 2026-10-11 (public, no auth):

  GET https://api.gemini.com/v1/prediction-markets/categories
      -> {"categories": ["Live", "Sports", ..., "Commodities", "Economics", ...]}
  GET https://api.gemini.com/v1/prediction-markets/events?category=Economics&status=active&limit=100
      -> {"data": [event...], "pagination": {"limit", "offset", "total"}}
      event:    id, title, ticker, category, status, expiryDate, contracts[...]
      contract: label, status, prices{bestBid, bestAsk, lastTradePrice, buy{yes,no}, sell{yes,no}}
                (prices are strings in [0,1]; either side can be null)

Two things differ from what the spec assumes:
  * The unfiltered listing does not return Economics events at all (1001 events, all Sports /
    Weather / Crypto / Commodities), so events must be requested per category.
  * There is no CPI, consumer-spending or retail-sale contract. The Economics category had 10
    events: Fed decisions, GDP, recession, unemployment, S&P/NASDAQ levels, Strait of Hormuz.

So `cpi_inflation_probability_next_30d` and `consumer_spending_expansion_odds` are reported as
unavailable (None) rather than approximated, and the signals that do exist are exposed under
their real names. None of them is a model input: no history of them exists to train on.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from ..config import GEMINI_BUDGET_S, GEMINI_CACHE_TTL_S

BASE_URL = "https://api.gemini.com"
EVENTS_PATH = "/v1/prediction-markets/events"
CATEGORIES_PATH = "/v1/prediction-markets/categories"
RELEVANT_CATEGORIES: tuple[str, ...] = ("Economics", "Commodities")

SPEC_SIGNALS_WITHOUT_A_CONTRACT = {
    "cpi_inflation_probability_next_30d": "Gemini lists no CPI / inflation contract",
    "consumer_spending_expansion_odds": "Gemini lists no consumer-spending or retail-sales contract",
}

FetchJson = Callable[[str, dict], Awaitable[dict]]


def implied_probability(prices: Optional[dict]) -> Optional[float]:
    """P_implied = (bid + ask) / 2. Falls back to the last trade when one side of the book is
    empty, and to None when there is neither a two-sided book nor a trade."""
    def num(value) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if 0.0 <= number <= 1.0 else None

    prices = prices or {}
    bid, ask = num(prices.get("bestBid")), num(prices.get("bestAsk"))
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    return num(prices.get("lastTradePrice"))


@dataclass
class MacroSnapshot:
    status: str                                   # "live" | "cached" | "unavailable"
    signals: dict[str, Optional[float]] = field(default_factory=dict)
    unavailable: dict[str, str] = field(default_factory=dict)
    events_seen: dict[str, int] = field(default_factory=dict)
    fetched_at: Optional[str] = None
    note: str = ""

    def model_features(self) -> dict[str, float]:
        """Values for the model's two Gemini feature slots. Empty today: neither has a contract."""
        return {name: self.signals[name] for name in SPEC_SIGNALS_WITHOUT_A_CONTRACT
                if self.signals.get(name) is not None}

    def to_json(self) -> dict:
        return {"source": "Gemini Prediction Markets", "status": self.status, "signals": self.signals,
                "unavailable": self.unavailable, "events_seen": self.events_seen,
                "fetched_at": self.fetched_at, "used_as_model_input": False, "note": self.note}


def _active_contracts(event: dict) -> list[dict]:
    return [c for c in event.get("contracts") or [] if c.get("status") in (None, "active")]


def _sum_labels(event: dict, pattern: str) -> Optional[float]:
    probs = [implied_probability(c.get("prices")) for c in _active_contracts(event)
             if re.search(pattern, c.get("label") or "", re.I)]
    probs = [p for p in probs if p is not None]
    return min(1.0, sum(probs)) if probs else None


def _expiry(event: dict) -> str:
    return event.get("expiryDate") or "9999"


def build_macro_signals(events: list[dict]) -> dict[str, Optional[float]]:
    """Macro sentiment vector from the contracts that really exist. Each value is an implied
    probability in [0, 1] or None when the contract is missing or has no usable price."""
    active = [e for e in events if e.get("status") == "active"]

    def by_ticker(prefix: str) -> list[dict]:
        return sorted((e for e in active if (e.get("ticker") or "").upper().startswith(prefix)), key=_expiry)

    signals: dict[str, Optional[float]] = {name: None for name in SPEC_SIGNALS_WITHOUT_A_CONTRACT}
    fed = by_ticker("FED")
    signals["fed_hike_probability_next_meeting"] = _sum_labels(fed[0], r"^hike") if fed else None
    signals["fed_cut_probability_next_meeting"] = _sum_labels(fed[0], r"^cut") if fed else None
    recession = by_ticker("RECESSION")
    signals["recession_probability"] = _sum_labels(recession[0], r"^yes$") if recession else None
    unemployment = by_ticker("UNEMP")
    signals["unemployment_above_5pct_probability"] = _sum_labels(unemployment[0], r"above 5%") if unemployment else None

    gdp = by_ticker("GDP")
    signals["gdp_growth_above_2pct_probability"] = None
    if gdp:
        high, total = 0.0, 0.0
        for contract in _active_contracts(gdp[0]):
            prob = implied_probability(contract.get("prices"))
            first_number = re.search(r"-?\d+(?:\.\d+)?", contract.get("label") or "")
            if prob is None or not first_number:
                continue
            total += prob
            label = (contract.get("label") or "").lower()
            lower_bound = float(first_number.group())
            if lower_bound > 2.0 and not re.search(r"below|under|less", label):
                high += prob
        if total > 0:
            signals["gdp_growth_above_2pct_probability"] = high / total  # buckets are exclusive
    return signals


async def _default_fetch_json(url: str, params: dict) -> dict:
    import aiohttp  # lazy: the deployed function uses the synchronous fetch in ml_engine.serving

    timeout = aiohttp.ClientTimeout(total=GEMINI_BUDGET_S)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            return await resp.json()


class GeminiPredictionClient:
    def __init__(self, fetch_json: Optional[FetchJson] = None, ttl_s: float = GEMINI_CACHE_TTL_S,
                 budget_s: float = GEMINI_BUDGET_S, clock: Callable[[], float] = time.monotonic,
                 categories: tuple[str, ...] = RELEVANT_CATEGORIES):
        self._fetch_json = fetch_json or _default_fetch_json
        self._ttl_s, self._budget_s, self._clock = ttl_s, budget_s, clock
        self._categories = categories
        self._cached: Optional[MacroSnapshot] = None
        self._cached_at = 0.0
        self._lock = asyncio.Lock()
        self.network_fetches = 0

    async def list_categories(self) -> list[str]:
        payload = await self._fetch_json(BASE_URL + CATEGORIES_PATH, {})
        return list(payload.get("categories") or [])

    async def list_events(self, category: str, status: str = "active", limit: int = 100) -> list[dict]:
        payload = await self._fetch_json(BASE_URL + EVENTS_PATH, {"category": category, "status": status, "limit": limit})
        return list(payload.get("data") or [])

    async def macro_snapshot(self) -> MacroSnapshot:
        """Cached for 15 minutes. Never raises: on failure returns the stale cache if there is
        one, else an explicit `unavailable` snapshot with no signal values."""
        if self._cached is not None and self._clock() - self._cached_at < self._ttl_s:
            return self._cached
        async with self._lock:
            if self._cached is not None and self._clock() - self._cached_at < self._ttl_s:
                return self._cached
            try:
                self.network_fetches += 1
                per_category = await asyncio.wait_for(
                    asyncio.gather(*(self.list_events(c) for c in self._categories)), timeout=self._budget_s)
            except Exception as err:
                reason = "timed out" if isinstance(err, asyncio.TimeoutError) else type(err).__name__
                if self._cached is not None:
                    stale = MacroSnapshot(**{**self._cached.__dict__, "status": "cached",
                                             "note": f"refresh failed ({reason}); serving a stale snapshot"})
                    return stale
                return MacroSnapshot(status="unavailable", unavailable=dict(SPEC_SIGNALS_WITHOUT_A_CONTRACT),
                                     note=f"Gemini request failed ({reason}); macro signals omitted")
            events = [e for chunk in per_category for e in chunk]
            snapshot = MacroSnapshot(
                status="live",
                signals=build_macro_signals(events),
                unavailable=dict(SPEC_SIGNALS_WITHOUT_A_CONTRACT),
                events_seen={c: len(chunk) for c, chunk in zip(self._categories, per_category)},
                fetched_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            self._cached, self._cached_at = snapshot, self._clock()
            return snapshot
