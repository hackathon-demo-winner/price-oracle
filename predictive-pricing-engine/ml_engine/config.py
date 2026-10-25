"""Constants shared by training, inference and the service."""
from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
WEIGHTS_DIR = PACKAGE_ROOT / "model" / "weights"
BLS_DIR = PROJECT_ROOT / "data" / "bls_cpi"

QUANTILES: tuple[float, ...] = (0.05, 0.25, 0.50, 0.75, 0.95)
QUANTILE_NAMES: tuple[str, ...] = ("p5", "p25", "p50", "p75", "p95")
HORIZON_DAYS = 30

# Latency budget from the spec (seconds).
INTERNAL_TIMEOUT_S = 35.0
SLA_S = 40.0
GEMINI_BUDGET_S = 1.5
HISTORY_BUDGET_S = 2.0
GEMINI_CACHE_TTL_S = 15 * 60

RETAILERS: tuple[str, ...] = ("amazon", "bestbuy", "newegg", "pcpartpicker", "bhphoto", "other")

# Product categories the model knows. Every one except "other" has a real BLS CPI sub-index.
PRODUCT_CATEGORIES: tuple[str, ...] = (
    "computer_hardware", "software", "phones_other_electronics", "televisions", "audio",
    "major_appliances", "small_appliances", "furniture", "home_goods", "tools",
    "personal_care", "beauty", "toys", "sporting_goods", "photo", "apparel", "footwear", "other",
)

# Feature order is the model's input contract (also used by the ONNX export).
FEATURES: tuple[str, ...] = (
    # price momentum & microstructure (NaN when no price history is available)
    "price_ratio_to_30d_avg",
    "price_ratio_to_90d_high",
    "price_ratio_to_all_time_low",
    "price_ratio_to_all_time_high",
    "price_ratio_to_all_time_avg",
    "rolling_7d_volatility",
    "rolling_30d_volatility",
    "days_since_last_price_change",
    "frequency_of_discounts_per_quarter",
    "history_days",
    # snapshot
    "price_ratio_to_regular",
    "log_price",
    # retailer & product meta
    "retailer_encoded",
    "product_category_encoded",
    "is_first_party_seller",
    # calendar
    "event_days_to_major_sale",
    "in_sale_window_now",
    "target_in_sale_window",
    "month",
    # real category trend (BLS CPI sub-index, as published at prediction time)
    "category_cpi_3m_change",
    "category_cpi_12m_change",
    # Gemini prediction-market signals. No historical values exist to train on, so these are
    # NaN in every training row and the trees never split on them. See README.
    "cpi_inflation_probability_next_30d",
    "consumer_spending_expansion_odds",
)

CATEGORICAL_FEATURES: tuple[str, ...] = ("retailer_encoded", "product_category_encoded")

# Features that have no training signal today (always NaN in training).
INERT_FEATURES: tuple[str, ...] = (
    "is_first_party_seller",
    "cpi_inflation_probability_next_30d",
    "consumer_spending_expansion_odds",
)
