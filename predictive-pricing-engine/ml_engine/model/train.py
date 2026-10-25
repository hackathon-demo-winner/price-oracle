"""Train the quantile gradient-boosting models and write weights + metadata.

    python -m ml_engine.model.train            # both models
    python -m ml_engine.model.train --products 1500

Two models are trained with the same code:

  product_30d        P5..P95 of log(price in 30 days / price now) for a single product.
                     Training data: SYNTHETIC (see data_pipeline/synthetic.py).
  category_index_1m  P5..P95 of the one-month log change of a category price index.
                     Training data: REAL (BLS CPI sub-indexes).

Both are evaluated by out-of-time walk-forward backtests; the weights that ship are those of
the last fold, so the last fold's test period is a true hold-out for the shipped model.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Optional, Sequence

import lightgbm as lgb
import numpy as np
import pandas as pd

from ..config import FEATURES, PROJECT_ROOT, QUANTILE_NAMES, QUANTILES
from ..data_pipeline.historical_fetcher import CategoryIndexStore
from ..data_pipeline.synthetic import DATA_LABEL as SYNTHETIC_LABEL
from ..data_pipeline.synthetic import build_synthetic_dataset
from .quantile_regressor import (CATEGORY_MODEL_DIR, PRODUCT_MODEL_DIR, QuantileModel, apply_conformal)

CACHE_DIR = PROJECT_ROOT / ".cache"
SYNTHETIC_SEED = 20261011
DEFAULT_PRODUCTS = 1500

PRODUCT_PARAMS = dict(learning_rate=0.05, num_leaves=31, min_data_in_leaf=200, feature_fraction=0.9,
                      bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0)
CATEGORY_PARAMS = dict(learning_rate=0.05, num_leaves=7, min_data_in_leaf=40, feature_fraction=0.9,
                       bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0)
PRODUCT_ROUNDS, CATEGORY_ROUNDS, EARLY_STOPPING = 300, 300, 40

PRODUCT_FOLDS = [("2022-01-01", "2023-01-01"), ("2023-01-01", "2024-01-01"),
                 ("2024-01-01", "2025-01-01"), ("2025-01-01", "2027-01-01")]
CATEGORY_FOLDS = [("2010-01-01", "2015-01-01"), ("2015-01-01", "2020-01-01"),
                  ("2020-01-01", "2023-01-01"), ("2023-01-01", "2027-01-01")]

CATEGORY_FEATURES = ("change_1m", "change_3m", "change_12m", "volatility_12m", "month", "category_encoded")


# ----------------------------------------------------------------------------- metrics

def pinball_loss(y: np.ndarray, pred: np.ndarray, q: float) -> float:
    diff = y - pred
    return float(np.mean(np.maximum(q * diff, (q - 1) * diff)))


def evaluate(y: np.ndarray, log_ratio_q: np.ndarray, current: Optional[np.ndarray] = None) -> dict:
    """Metrics for predicted log-ratio quantiles (n x 5). Price metrics need `current`."""
    out = {"n": int(len(y))}
    losses = {name: pinball_loss(y, log_ratio_q[:, i], q) for i, (name, q) in enumerate(zip(QUANTILE_NAMES, QUANTILES))}
    out["pinball_loss"] = losses
    out["pinball_loss_mean"] = float(np.mean(list(losses.values())))
    out["coverage_p5_p95"] = float(np.mean((y >= log_ratio_q[:, 0]) & (y <= log_ratio_q[:, 4])))
    out["coverage_p25_p75"] = float(np.mean((y >= log_ratio_q[:, 1]) & (y <= log_ratio_q[:, 3])))
    out["mean_width_p5_p95_pct"] = float(np.mean(np.exp(log_ratio_q[:, 4]) - np.exp(log_ratio_q[:, 0])) * 100)
    pct_err = (np.exp(log_ratio_q[:, 2]) - np.exp(y)) * 100  # error of P50 as % of current price
    out["mae_pct_of_current"] = float(np.mean(np.abs(pct_err)))
    out["rmse_pct_of_current"] = float(np.sqrt(np.mean(pct_err ** 2)))
    naive_err = (1 - np.exp(y)) * 100                       # "price will not change" forecast
    out["no_change_mae_pct_of_current"] = float(np.mean(np.abs(naive_err)))
    out["no_change_rmse_pct_of_current"] = float(np.sqrt(np.mean(naive_err ** 2)))
    if current is not None:
        err = current * (np.exp(log_ratio_q[:, 2]) - np.exp(y))
        out["mae_usd"] = float(np.mean(np.abs(err)))
        out["rmse_usd"] = float(np.sqrt(np.mean(err ** 2)))
    return out


# ----------------------------------------------------------------------------- fitting

def fit(train: pd.DataFrame, features: Sequence[str], params: dict, rounds: int, metadata: dict) -> QuantileModel:
    """Fit five quantile boosters. `train` must be sorted by time: the first 80% fits the
    trees, the next 10% drives early stopping, the last 10% calibrates the conformal offsets."""
    n = len(train)
    a, b = int(n * 0.8), int(n * 0.9)
    fit_part, valid_part, calib_part = train.iloc[:a], train.iloc[a:b], train.iloc[b:]
    feats = list(features)
    dtrain = lgb.Dataset(fit_part[feats].to_numpy(np.float64), label=fit_part["y"].to_numpy(), feature_name=feats, free_raw_data=False)
    dvalid = lgb.Dataset(valid_part[feats].to_numpy(np.float64), label=valid_part["y"].to_numpy(), reference=dtrain)

    boosters, curves = [], {}
    for name, q in zip(QUANTILE_NAMES, QUANTILES):
        history: dict = {}
        booster = lgb.train(
            dict(params, objective="quantile", alpha=q, metric="quantile", verbose=-1, seed=7,
                 num_threads=4, deterministic=True, force_row_wise=True),
            dtrain, num_boost_round=rounds, valid_sets=[dvalid], valid_names=["valid"],
            callbacks=[lgb.early_stopping(EARLY_STOPPING, verbose=False), lgb.record_evaluation(history)],
        )
        curve = history["valid"]["quantile"]
        curves[name] = {"first_iteration": float(curve[0]), "best_iteration": int(booster.best_iteration),
                        "best": float(min(curve)), "rounds_run": len(curve)}
        boosters.append(booster)

    model = QuantileModel(boosters, feats, {}, metadata)
    raw = np.sort(model.raw_log_ratio(calib_part[feats].to_numpy(np.float64)), axis=1)
    y = calib_part["y"].to_numpy()
    offsets = {}
    for key, lo, hi, level in (("interval_90", 0, 4, 0.90), ("interval_50", 1, 3, 0.50)):
        scores = np.maximum(raw[:, lo] - y, y - raw[:, hi])
        offsets[key] = float(np.quantile(scores, min(1.0, level * (1 + 1 / len(y)))))
    model.offsets = offsets
    model.metadata.update({
        "validation_pinball_curves": curves,
        "rows": {"fit": len(fit_part), "early_stopping": len(valid_part), "conformal_calibration": len(calib_part)},
        "naive_unconditional_quantiles": [float(np.quantile(fit_part["y"], q)) for q in QUANTILES],
    })
    return model


def naive_baseline(train_y: np.ndarray, test_y: np.ndarray) -> dict:
    """Pinball loss of constant predictions = unconditional training quantiles."""
    losses = {name: pinball_loss(test_y, np.full(len(test_y), np.quantile(train_y, q)), q)
              for name, q in zip(QUANTILE_NAMES, QUANTILES)}
    return {"pinball_loss": losses, "pinball_loss_mean": float(np.mean(list(losses.values())))}


def walk_forward(frame: pd.DataFrame, features: Sequence[str], folds, params: dict, rounds: int,
                 metadata: dict, group_col: Optional[str] = None) -> tuple[list[dict], QuantileModel, pd.DataFrame]:
    """Expanding-window out-of-time backtest. For each fold the model sees only rows whose
    *target* date is before the fold starts, and is tested on rows anchored inside the fold.
    Returns (fold reports, the last fold's model, the last fold's test rows)."""
    frame = frame.sort_values("target_date", kind="stable").reset_index(drop=True)
    reports, model, test = [], None, None
    for start, end in folds:
        start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
        train = frame[frame["target_date"] < start_ts]
        test = frame[(frame["as_of"] >= start_ts) & (frame["as_of"] < end_ts)]
        if len(train) < 500 or len(test) < 50:
            raise ValueError(f"fold {start}..{end} has too little data (train={len(train)}, test={len(test)})")
        model = fit(train, features, params, rounds, dict(metadata, trained_on_targets_before=start))
        X, y = test[list(features)].to_numpy(np.float64), test["y"].to_numpy()
        current = test["current_price"].to_numpy() if "current_price" in test else None
        report = {"test_period": [start, end], "train_rows": len(train),
                  "model": evaluate(y, model.predict_log_ratio(X), current),
                  "model_before_conformal": evaluate(y, apply_conformal(model.raw_log_ratio(X), {}), current),
                  "naive_unconditional_quantiles": naive_baseline(train["y"].to_numpy(), y),
                  "conformal_offsets": model.offsets}
        if group_col:
            report["by_" + group_col] = {
                str(key): evaluate(part["y"].to_numpy(), model.predict_log_ratio(part[list(features)].to_numpy(np.float64)),
                                   part["current_price"].to_numpy() if "current_price" in part else None)
                for key, part in test.groupby(group_col)}
        reports.append(report)
    return reports, model, test


# ----------------------------------------------------------------------------- datasets

def load_synthetic_dataset(n_products: int = DEFAULT_PRODUCTS, seed: int = SYNTHETIC_SEED, use_cache: bool = True) -> pd.DataFrame:
    path = CACHE_DIR / f"synthetic_{n_products}_{seed}.pkl"
    if use_cache and path.exists():
        return pd.read_pickle(path)
    frame = build_synthetic_dataset(n_products=n_products, seed=seed)
    if use_cache:
        CACHE_DIR.mkdir(exist_ok=True)
        frame.to_pickle(path)
    return frame


def build_category_index_dataset(cpi: Optional[CategoryIndexStore] = None) -> pd.DataFrame:
    """REAL data: one row per (BLS category series, month) with the next month's log change
    as target. The one month BLS did not publish (Oct 2025) is bridged for trailing-change
    features only; it is never a target or an anchor."""
    cpi = cpi or CategoryIndexStore()
    rows = []
    for code, category in enumerate(cpi.categories):
        dates, values = cpi.monthly(category)
        ordinal = np.array([d.year * 12 + d.month for d in dates])
        for i in range(13, len(dates) - 1):
            if ordinal[i + 1] - ordinal[i - 13] != 14:  # 15 consecutive months required
                continue
            if cpi.is_interpolated(category, dates[i]) or cpi.is_interpolated(category, dates[i + 1]):
                continue  # never anchor on, or score against, a month BLS did not publish
            feats = CategoryIndexStore.index_features(values, i)
            feats.update({"month": float(dates[i].month), "category_encoded": float(code),
                          "y": float(np.log(values[i + 1] / values[i])),
                          "as_of": pd.Timestamp(dates[i]), "target_date": pd.Timestamp(dates[i + 1]),
                          "category": category})
            rows.append(feats)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- entry points

def train_product_model(n_products: int = DEFAULT_PRODUCTS, save: bool = True) -> dict:
    frame = load_synthetic_dataset(n_products)
    meta = {"model": "product_30d", "target": "log(price_t+30d / price_t)", "horizon_days": 30,
            "training_data": SYNTHETIC_LABEL, "training_data_is_synthetic": True,
            "synthetic_products": n_products, "synthetic_seed": SYNTHETIC_SEED, "dataset_rows": len(frame),
            "warning": "Trained on simulated prices. Backtest metrics measure fit to the simulator, not real-world accuracy.",
            "lightgbm_params": PRODUCT_PARAMS}
    reports, model, _ = walk_forward(frame, FEATURES, PRODUCT_FOLDS, PRODUCT_PARAMS, PRODUCT_ROUNDS, meta, group_col="history_mode")
    model.metadata.update({"walk_forward_backtest": reports, "holdout_period": PRODUCT_FOLDS[-1],
                           "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    if save:
        model.save(PRODUCT_MODEL_DIR)
    return model.metadata


def train_category_index_model(save: bool = True) -> dict:
    cpi = CategoryIndexStore()
    frame = build_category_index_dataset(cpi)
    meta = {"model": "category_index_1m", "target": "log(index_next_month / index_this_month)",
            "training_data": f"REAL: {cpi.source}", "training_data_is_synthetic": False,
            "categories": cpi.categories, "series": cpi.series_meta, "dataset_rows": len(frame),
            "warning": "Category-level index, not an individual product's price.",
            "lightgbm_params": CATEGORY_PARAMS}
    reports, model, _ = walk_forward(frame, CATEGORY_FEATURES, CATEGORY_FOLDS, CATEGORY_PARAMS, CATEGORY_ROUNDS, meta)
    model.metadata.update({"walk_forward_backtest": reports, "holdout_period": CATEGORY_FOLDS[-1],
                           "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    if save:
        model.save(CATEGORY_MODEL_DIR)
    return model.metadata


def _print_reports(title: str, meta: dict) -> None:
    print(f"\n=== {title} ===\ntraining data: {meta['training_data']}\nrows: {meta['dataset_rows']}")
    for rep in meta["walk_forward_backtest"]:
        m, base = rep["model"], rep["naive_unconditional_quantiles"]
        print(f"  test {rep['test_period'][0]}..{rep['test_period'][1]}  n={m['n']:6d}  train_rows={rep['train_rows']:6d}  "
              f"pinball={m['pinball_loss_mean']:.5f} (naive {base['pinball_loss_mean']:.5f})  "
              f"cov[P5,P95]={m['coverage_p5_p95']:.3f}  cov[P25,P75]={m['coverage_p25_p75']:.3f}  "
              f"MAE={m['mae_pct_of_current']:.2f}% RMSE={m['rmse_pct_of_current']:.2f}% "
              f"(no-change MAE {m['no_change_mae_pct_of_current']:.2f}%)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--products", type=int, default=DEFAULT_PRODUCTS, help="number of synthetic products")
    parser.add_argument("--only", choices=["product", "category"], help="train just one model")
    args = parser.parse_args()
    summary = {}
    if args.only != "category":
        summary["product_30d"] = train_product_model(args.products)
        _print_reports("product_30d  [SYNTHETIC DATA - not evidence of real-world accuracy]", summary["product_30d"])
    if args.only != "product":
        summary["category_index_1m"] = train_category_index_model()
        _print_reports("category_index_1m  [REAL BLS CPI data, category level]", summary["category_index_1m"])
    print(f"\nweights written under {PRODUCT_MODEL_DIR.parent}")


if __name__ == "__main__":
    main()
