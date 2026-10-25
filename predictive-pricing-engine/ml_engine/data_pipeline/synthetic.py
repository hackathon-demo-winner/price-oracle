"""SYNTHETIC product price generator. Read this before trusting any number that comes from it.

No free, licensed source of product-level daily price histories was found (see README), so
the product model is trained on simulated series. What is real and what is invented:

  REAL      the category price level each product follows: the BLS CPI sub-index of its
            category, over the actual calendar dates of the simulated series.
  REAL      the sale calendar (Black Friday / Cyber Monday exact; Prime Day, Prime Big Deal
            Days and back-to-school at their typical dates).
  INVENTED  everything product-specific: how often a product is discounted, how deep, for
            how long, how sticky its regular price is, shortage spikes, retailer repricing
            noise. The parameters below are assumptions, not estimates from data.

A model fitted to this learns the generator. Backtests on it show the pipeline works and is
calibrated *against the generator*; they say nothing about accuracy on real products.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from ..config import FEATURES, HORIZON_DAYS, PRODUCT_CATEGORIES, RETAILERS
from .feature_engineering import (FeatureInputs, HistorySummary, PriceHistory, build_features, in_sale_window)
from .historical_fetcher import CategoryIndexStore

DATA_LABEL = "SYNTHETIC (semi-synthetic: real BLS category trend + real sale calendar + invented product dynamics)"


@dataclass(frozen=True)
class Profile:
    promo_hazard: float      # chance per ordinary day that a promotion starts
    sale_participation: float  # chance the product joins a given major sale event
    depth_mean: float        # mean promotional discount
    depth_sd: float
    reprice_rate: float      # chance per day that the regular price is re-set
    shock_rate: float        # chance per day that a shortage spike starts


# INVENTED parameters, by broad group.
_GROUPS: dict[str, Profile] = {
    "electronics": Profile(0.012, 0.55, 0.15, 0.06, 1 / 40, 1 / 900),
    "appliances": Profile(0.010, 0.45, 0.17, 0.07, 1 / 60, 1 / 2000),
    "soft_goods": Profile(0.018, 0.50, 0.28, 0.10, 1 / 75, 1 / 5000),
    "personal": Profile(0.022, 0.35, 0.12, 0.05, 1 / 50, 1 / 3000),
    "home": Profile(0.012, 0.40, 0.18, 0.07, 1 / 60, 1 / 3000),
}
_CATEGORY_GROUP = {
    "computer_hardware": "electronics", "software": "electronics", "phones_other_electronics": "electronics",
    "televisions": "electronics", "audio": "electronics", "photo": "electronics",
    "major_appliances": "appliances", "small_appliances": "appliances",
    "apparel": "soft_goods", "footwear": "soft_goods",
    "personal_care": "personal", "beauty": "personal",
    "furniture": "home", "home_goods": "home", "tools": "home", "toys": "home", "sporting_goods": "home",
}
_RETAILER_JITTER = {"amazon": 0.20, "newegg": 0.06, "pcpartpicker": 0.10, "bestbuy": 0.0, "bhphoto": 0.02, "other": 0.03}


@dataclass
class SyntheticProduct:
    product_id: int
    category: str
    retailer: str
    start: date
    prices: np.ndarray          # daily price
    regular: np.ndarray         # daily undiscounted price
    on_promo: np.ndarray        # bool
    shows_list_price: bool


def simulate_product(rng: np.random.Generator, product_id: int, cpi: CategoryIndexStore,
                     first_start: date, last_end: date) -> SyntheticProduct:
    category = str(rng.choice([c for c in PRODUCT_CATEGORIES if c != "other"]))
    retailer = str(rng.choice(RETAILERS, p=[0.40, 0.22, 0.10, 0.10, 0.08, 0.10]))
    base = _GROUPS[_CATEGORY_GROUP[category]]
    n_days = int(rng.integers(400, 900))
    latest_start = (last_end - first_start).days - n_days
    start = first_start + timedelta(days=int(rng.integers(0, latest_start)))
    days = [start + timedelta(days=i) for i in range(n_days)]

    # Product heterogeneity around the group profile.
    promo_hazard = base.promo_hazard * float(rng.lognormal(0, 0.7))
    participation = float(np.clip(base.sale_participation + rng.normal(0, 0.25), 0.0, 1.0))
    depth_mean = float(np.clip(base.depth_mean * rng.lognormal(0, 0.35), 0.03, 0.6))
    reprice_rate = base.reprice_rate * float(rng.lognormal(0, 0.5))
    jitter_rate = _RETAILER_JITTER[retailer]

    list_price = float(np.exp(rng.normal(4.6, 1.1)))  # median ~ $100
    level = cpi.daily_level(category, days)
    category_path = np.log(level / level[0])           # REAL category trend
    idio = 0.0                                         # slow product-specific drift (random walk)
    regular_log = np.log(list_price)

    in_window = np.array([in_sale_window(d) for d in days])
    window_id = np.cumsum(np.concatenate([[0], np.diff(in_window.astype(int)) == 1]))
    joined: dict[int, bool] = {}

    prices = np.empty(n_days)
    regular = np.empty(n_days)
    on_promo = np.zeros(n_days, dtype=bool)
    promo_left, promo_depth, shock_left, shock_size = 0, 0.0, 0, 0.0

    for t in range(n_days):
        idio += rng.normal(0, 0.0015)
        if rng.random() < reprice_rate:
            regular_log = np.log(list_price) + category_path[t] + idio + rng.normal(0, 0.015)

        if shock_left == 0 and rng.random() < base.shock_rate:
            shock_left, shock_size = int(rng.integers(20, 80)), float(rng.uniform(0.10, 0.35))
        if promo_left == 0:
            if in_window[t]:
                wid = int(window_id[t])
                if wid not in joined:
                    joined[wid] = rng.random() < participation
                    if joined[wid]:
                        promo_left = int(rng.integers(4, 12))
                        promo_depth = float(np.clip(rng.normal(depth_mean * 1.25, base.depth_sd), 0.03, 0.7))
            elif shock_left == 0 and rng.random() < promo_hazard:
                promo_left = 1 + int(rng.geometric(1 / 6))
                promo_depth = float(np.clip(rng.normal(depth_mean, base.depth_sd), 0.03, 0.7))

        price_log = regular_log
        if shock_left > 0:
            price_log += np.log1p(shock_size)
            shock_left -= 1
        if promo_left > 0:
            price_log += np.log1p(-promo_depth)
            on_promo[t] = True
            promo_left -= 1
        if jitter_rate and rng.random() < jitter_rate:
            price_log += rng.normal(0, 0.008)

        regular[t] = round(float(np.exp(regular_log)), 2)
        prices[t] = max(0.5, round(float(np.exp(price_log)), 2))

    return SyntheticProduct(product_id, category, retailer, start, prices, regular, on_promo,
                            shows_list_price=bool(rng.random() < 0.5))


def _mask_mode(rng: np.random.Generator) -> str:
    """How much history the model is shown for a training row. Production today is almost
    always `snapshot_only`, so that mode is heavily represented."""
    return str(rng.choice(["full_history", "summary_only", "snapshot_only"], p=[0.45, 0.15, 0.40]))


def rows_for_product(rng: np.random.Generator, product: SyntheticProduct, cpi: CategoryIndexStore,
                     step: int = 6, force_mode: Optional[str] = None) -> list[dict]:
    rows = []
    n = len(product.prices)
    for t in range(120 + int(rng.integers(0, step)), n - HORIZON_DAYS, step):
        price = float(product.prices[t])
        as_of = product.start + timedelta(days=t)
        mode = force_mode or _mask_mode(rng)
        past = product.prices[:t]
        history, summary = None, None
        if mode == "full_history":
            keep = int(rng.integers(30, t + 1))  # trackers start at different times
            history = PriceHistory(prices=past[-keep:], source="synthetic", is_synthetic=True)
        elif mode == "summary_only":
            summary = HistorySummary(float(past.min()), float(past.max()), float(past.mean()), source="synthetic")

        regular_price = None
        if product.on_promo[t] and rng.random() < 0.7:
            regular_price = float(product.regular[t])
        elif product.shows_list_price and product.regular[t] > price * 1.005:
            regular_price = float(product.regular[t])

        cpi_3m, cpi_12m = cpi.trend(product.category, as_of)
        feats = build_features(FeatureInputs(
            current_price=price, as_of=as_of, retailer=product.retailer, category=product.category,
            regular_price=regular_price, history=history, summary=summary,
            category_cpi_3m_change=cpi_3m, category_cpi_12m_change=cpi_12m,
        ))
        future = float(product.prices[t + HORIZON_DAYS])
        feats.update({
            "y": float(np.log(future / price)), "current_price": price, "future_price": future,
            "as_of": pd.Timestamp(as_of), "target_date": pd.Timestamp(as_of + timedelta(days=HORIZON_DAYS)),
            "product_id": product.product_id, "history_mode": mode,
        })
        rows.append(feats)
    return rows


def build_synthetic_dataset(n_products: int = 1500, seed: int = 20261011, step: int = 6,
                            first_start: date = date(2014, 1, 1), last_end: date = date(2026, 8, 1),
                            cpi: Optional[CategoryIndexStore] = None) -> pd.DataFrame:
    """Deterministic for a given seed. Returns one row per (product, anchor day)."""
    cpi = cpi or CategoryIndexStore()
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    for pid in range(n_products):
        product = simulate_product(rng, pid, cpi, first_start, last_end)
        rows.extend(rows_for_product(rng, product, cpi, step=step))
    frame = pd.DataFrame(rows)
    frame.attrs["data_label"] = DATA_LABEL
    return frame[list(FEATURES) + ["y", "current_price", "future_price", "as_of", "target_date", "product_id", "history_mode"]]
