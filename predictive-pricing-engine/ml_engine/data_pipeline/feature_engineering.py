"""Builds the model's feature vector from a product snapshot, optional price history,
the sale calendar, the real category CPI trend and (optional) Gemini macro signals.

Every history feature is NaN when there is no history: LightGBM treats NaN as "missing"
and was trained with history masked on a share of rows, so the model degrades to wider
intervals instead of consuming an invented history.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Mapping, Optional, Sequence

import numpy as np

from ..config import FEATURES, HORIZON_DAYS, PRODUCT_CATEGORIES, RETAILERS

NAN = float("nan")
SALE_WINDOW_HALF_WIDTH_DAYS = 4
DISCOUNT_THRESHOLD = 0.05  # a "discount" is a day priced >= 5% under the trailing 90-day high


# ----------------------------------------------------------------------------- calendar

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def major_sale_dates(year: int) -> list[date]:
    """Anchor dates of the recurring U.S. retail sale events in a year.

    Black Friday and Cyber Monday are exact. Prime Day, Prime Big Deal Days and
    back-to-school are announced each year by the retailer; the dates used here are the
    typical ones (second Tuesday of July, second Tuesday of October, August 1).
    """
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    return [
        _nth_weekday(year, 7, 1, 2),          # Prime Day (typical)
        date(year, 8, 1),                     # back to school (typical)
        _nth_weekday(year, 10, 1, 2),         # Prime Big Deal Days (typical)
        thanksgiving + timedelta(days=1),     # Black Friday
        thanksgiving + timedelta(days=4),     # Cyber Monday
    ]


def days_to_next_major_sale(day: date) -> int:
    upcoming = [d for y in (day.year, day.year + 1) for d in major_sale_dates(y) if d >= day]
    return (min(upcoming) - day).days


def in_sale_window(day: date) -> bool:
    return any(abs((day - d).days) <= SALE_WINDOW_HALF_WIDTH_DAYS
               for y in (day.year - 1, day.year, day.year + 1) for d in major_sale_dates(y))


# ----------------------------------------------------------------------------- inputs

@dataclass
class HistorySummary:
    """Summary statistics of a price history when the full series is not available
    (e.g. a tracker page that prints all-time low / high / average)."""
    all_time_low: Optional[float] = None
    all_time_high: Optional[float] = None
    all_time_avg: Optional[float] = None
    source: str = "unknown"


@dataclass
class PriceHistory:
    """Daily prices, oldest first, ending the day before (or on) the prediction date."""
    prices: Sequence[float] = field(default_factory=list)
    source: str = "unknown"
    is_synthetic: bool = False

    def __len__(self) -> int:
        return len(self.prices)


@dataclass
class FeatureInputs:
    current_price: float
    as_of: date
    retailer: str = "other"
    category: str = "other"
    regular_price: Optional[float] = None
    is_first_party_seller: Optional[bool] = None
    history: Optional[PriceHistory] = None
    summary: Optional[HistorySummary] = None
    category_cpi_3m_change: float = NAN
    category_cpi_12m_change: float = NAN
    macro: Mapping[str, float] = field(default_factory=dict)


# ----------------------------------------------------------------------------- encoders

def encode_retailer(retailer: str) -> int:
    return RETAILERS.index(retailer) if retailer in RETAILERS else RETAILERS.index("other")


def encode_category(category: str) -> int:
    return PRODUCT_CATEGORIES.index(category) if category in PRODUCT_CATEGORIES else PRODUCT_CATEGORIES.index("other")


_RETAILER_HOSTS = (
    ("amazon.", "amazon"), ("bestbuy.", "bestbuy"), ("newegg.", "newegg"),
    ("pcpartpicker.", "pcpartpicker"), ("bhphotovideo.", "bhphoto"),
)


def retailer_from_url(url: str) -> str:
    host = re.sub(r"^https?://", "", url.strip().lower()).split("/")[0]
    for needle, name in _RETAILER_HOSTS:
        if needle in host:
            return name
    return "other"


# Ordered: the first matching rule wins. Matched against "<category> <name>" lower-cased.
_CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    (r"\b(ddr[345]|ram\b|memory|ssd|nvme|hard drive|hdd|gpu|graphics card|geforce|radeon|processor|ryzen|core i[3579]|"
     r"motherboard|power supply|laptop|notebook|desktop|monitor|keyboard|mouse|router|webcam|printer|computer|cpu)", "computer_hardware"),
    (r"\b(airpods|earbuds?|headphones?|headset|speakers?|soundbar|subwoofer|turntable|receiver|audio)", "audio"),
    (r"\b(television|\btv\b|oled|qled)", "televisions"),
    (r"\b(camera|lens|tripod|photo|camcorder|gopro)", "photo"),
    (r"\b(iphone|smartphone|cell ?phone|galaxy s\d|pixel \d|tablet|ipad|kindle|smart ?watch|calculator)", "phones_other_electronics"),
    (r"\b(software|antivirus|office 365|subscription code)", "software"),
    (r"\b(refrigerator|washer|dryer|dishwasher|range|oven|freezer)", "major_appliances"),
    (r"\b(vacuum|blender|air fryer|coffee|espresso|toaster|microwave|mixer|kettle|instant pot|appliance)", "small_appliances"),
    (r"\b(sofa|couch|mattress|desk|chair|bed frame|dresser|bookcase|furniture)", "furniture"),
    (r"\b(drill|saw|wrench|tool|mower|grill|hardware)", "tools"),
    (r"\b(makeup|cosmetic|perfume|fragrance|lipstick|mascara|skincare|serum|moisturizer|beauty)", "beauty"),
    (r"\b(shampoo|toothbrush|toothpaste|razor|shaver|deodorant|hair dryer|trimmer|personal care)", "personal_care"),
    (r"\b(lego|toy|doll|playset|board game|puzzle|console|playstation|ps5|xbox|nintendo|video game)", "toys"),
    (r"\b(bike|bicycle|treadmill|dumbbell|yoga|tent|fitness|golf|sporting)", "sporting_goods"),
    (r"\b(sneakers?|boots?|shoes?|sandals?|footwear)", "footwear"),
    (r"\b(shirt|jacket|jeans|dress|hoodie|pants|apparel|clothing)", "apparel"),
    (r"\b(bedding|sheets?|towels?|cookware|lamp|rug|curtain|storage|kitchen|home)", "home_goods"),
)


def classify_category(name: str = "", scraped_category: Optional[str] = None) -> str:
    """Keyword classifier from a product title / scraped category to a model category."""
    text = f"{scraped_category or ''} {name or ''}".lower()
    for pattern, category in _CATEGORY_RULES:
        if re.search(pattern, text):
            return category
    return "other"


# ----------------------------------------------------------------------------- features

def _safe_ratio(numerator: float, denominator: Optional[float]) -> float:
    if denominator is None or not denominator or not math.isfinite(denominator) or denominator <= 0:
        return NAN
    return numerator / denominator


def history_features(current_price: float, history: Optional[PriceHistory]) -> dict[str, float]:
    names = ("price_ratio_to_30d_avg", "price_ratio_to_90d_high", "price_ratio_to_all_time_low",
             "price_ratio_to_all_time_high", "price_ratio_to_all_time_avg", "rolling_7d_volatility",
             "rolling_30d_volatility", "days_since_last_price_change", "frequency_of_discounts_per_quarter",
             "history_days")
    out = {n: NAN for n in names}
    if history is None or len(history) < 8:
        return out
    prices = np.asarray(list(history.prices) + [current_price], dtype=float)
    prices = prices[np.isfinite(prices) & (prices > 0)]
    n = len(prices)
    if n < 8:
        return out
    log_returns = np.diff(np.log(prices))
    out["history_days"] = float(n - 1)
    out["price_ratio_to_30d_avg"] = current_price / prices[-31:-1].mean()
    out["price_ratio_to_90d_high"] = current_price / prices[-91:].max()
    out["price_ratio_to_all_time_low"] = current_price / prices.min()
    out["price_ratio_to_all_time_high"] = current_price / prices.max()
    out["price_ratio_to_all_time_avg"] = current_price / prices.mean()
    out["rolling_7d_volatility"] = float(log_returns[-7:].std())
    out["rolling_30d_volatility"] = float(log_returns[-30:].std())
    changed = np.nonzero(np.abs(log_returns) > 1e-9)[0]
    out["days_since_last_price_change"] = float(len(log_returns) - 1 - changed[-1]) if len(changed) else float(len(log_returns))

    # Discount episodes in the last quarter: runs of days >= 5% below the trailing 90-day high.
    tail = prices[-181:]
    padded = np.concatenate([np.full(181 - len(tail), -np.inf), tail])
    trailing_high = np.lib.stride_tricks.sliding_window_view(padded, 91).max(axis=1)
    window = padded[-91:]
    valid = np.isfinite(window)
    discounted = valid & (window < trailing_high * (1 - DISCOUNT_THRESHOLD))
    episodes = int(discounted[0]) + int(np.sum(discounted[1:] & ~discounted[:-1]))
    out["frequency_of_discounts_per_quarter"] = episodes * 90.0 / int(valid.sum())
    return out


def build_features(inputs: FeatureInputs) -> dict[str, float]:
    price = float(inputs.current_price)
    if not math.isfinite(price) or price <= 0:
        raise ValueError("current_price must be a positive number")

    feats = history_features(price, inputs.history)
    if inputs.summary is not None:
        # A summary only fills the features it can support; it never overrides a full history.
        fills = (("price_ratio_to_all_time_low", inputs.summary.all_time_low),
                 ("price_ratio_to_all_time_high", inputs.summary.all_time_high),
                 ("price_ratio_to_all_time_avg", inputs.summary.all_time_avg))
        for name, value in fills:
            if math.isnan(feats[name]):
                feats[name] = _safe_ratio(price, value)

    feats["price_ratio_to_regular"] = _safe_ratio(price, inputs.regular_price)
    feats["log_price"] = math.log(price)
    feats["retailer_encoded"] = float(encode_retailer(inputs.retailer))
    feats["product_category_encoded"] = float(encode_category(inputs.category))
    feats["is_first_party_seller"] = NAN if inputs.is_first_party_seller is None else float(inputs.is_first_party_seller)

    feats["event_days_to_major_sale"] = float(days_to_next_major_sale(inputs.as_of))
    feats["in_sale_window_now"] = float(in_sale_window(inputs.as_of))
    feats["target_in_sale_window"] = float(in_sale_window(inputs.as_of + timedelta(days=HORIZON_DAYS)))
    feats["month"] = float(inputs.as_of.month)

    feats["category_cpi_3m_change"] = float(inputs.category_cpi_3m_change)
    feats["category_cpi_12m_change"] = float(inputs.category_cpi_12m_change)

    for name in ("cpi_inflation_probability_next_30d", "consumer_spending_expansion_odds"):
        value = inputs.macro.get(name) if inputs.macro else None
        feats[name] = NAN if value is None else float(value)

    return {name: feats[name] for name in FEATURES}


def feature_vector(inputs: FeatureInputs) -> np.ndarray:
    feats = build_features(inputs)
    return np.array([feats[name] for name in FEATURES], dtype=np.float64)
