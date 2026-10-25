"""Quantile loss and out-of-time walk-forward backtests.

Two models are backtested, and the data behind each is different in kind:

  product_30d        SYNTHETIC data. Passing here shows the pipeline trains, converges and is
                     calibrated against the simulator. It is NOT evidence of real-world accuracy.
  category_index_1m  REAL BLS CPI sub-indexes (category level, one month ahead).

The walk-forward is re-run from scratch here (about a minute); nothing is read from the saved
metadata except to check that the shipped weights reproduce. Thresholds are the spec's:
coverage of [P5, P95] >= 0.88, asserted separately for every out-of-time fold.
"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import record_result
from ml_engine.config import FEATURES, QUANTILE_NAMES
from ml_engine.model import train
from ml_engine.model.quantile_regressor import PRODUCT_MODEL_DIR, QuantileModel, enforce_monotonic

COVERAGE_FLOOR = 0.88
N_FOLDS = 4


@pytest.fixture(scope="module")
def product_run() -> dict:
    meta = train.train_product_model(save=False)
    record_result("backtest_product_30d_SYNTHETIC", {k: meta[k] for k in (
        "training_data", "training_data_is_synthetic", "dataset_rows", "walk_forward_backtest", "validation_pinball_curves")})
    return meta


@pytest.fixture(scope="module")
def category_run() -> dict:
    meta = train.train_category_index_model(save=False)
    record_result("backtest_category_index_1m_REAL", {k: meta[k] for k in (
        "training_data", "training_data_is_synthetic", "dataset_rows", "walk_forward_backtest", "validation_pinball_curves")})
    return meta


def _summary(label: str, meta: dict) -> str:
    lines = [f"\n[{label}] {meta['training_data']}"]
    for rep in meta["walk_forward_backtest"]:
        m = rep["model"]
        lines.append(f"  {rep['test_period'][0]}..{rep['test_period'][1]} n={m['n']} pinball={m['pinball_loss_mean']:.5f} "
                     f"naive={rep['naive_unconditional_quantiles']['pinball_loss_mean']:.5f} "
                     f"cov90={m['coverage_p5_p95']:.3f} cov50={m['coverage_p25_p75']:.3f} "
                     f"MAE={m['mae_pct_of_current']:.2f}% RMSE={m['rmse_pct_of_current']:.2f}%"
                     + (f" MAE=${m['mae_usd']:.2f} RMSE=${m['rmse_usd']:.2f}" if "mae_usd" in m else ""))
    return "\n".join(lines)


# ----------------------------------------------------------------------------- product model (SYNTHETIC)

def test_product_backtest_is_labelled_synthetic(product_run):
    print(_summary("SYNTHETIC", product_run))
    assert product_run["training_data_is_synthetic"] is True
    assert "SYNTHETIC" in product_run["training_data"]


def test_product_pinball_loss_converges(product_run):
    """Validation pinball loss falls during boosting for every quantile."""
    for name, curve in product_run["validation_pinball_curves"].items():
        assert curve["best"] < curve["first_iteration"], name


@pytest.mark.parametrize("fold", range(N_FOLDS))
def test_product_pinball_beats_unconditional_quantiles(product_run, fold):
    rep = product_run["walk_forward_backtest"][fold]
    assert rep["model"]["pinball_loss_mean"] < rep["naive_unconditional_quantiles"]["pinball_loss_mean"]


@pytest.mark.parametrize("fold", range(N_FOLDS))
def test_product_p5_p95_coverage_synthetic(product_run, fold):
    rep = product_run["walk_forward_backtest"][fold]
    assert rep["model"]["coverage_p5_p95"] >= COVERAGE_FLOOR, rep["test_period"]


@pytest.mark.parametrize("fold", range(N_FOLDS))
def test_product_median_beats_no_change_forecast(product_run, fold):
    m = product_run["walk_forward_backtest"][fold]["model"]
    assert m["mae_pct_of_current"] < m["no_change_mae_pct_of_current"]


def test_shipped_product_weights_on_their_holdout():
    """The saved weights never saw the last fold. Monotonic, covered, and same as a fresh run."""
    model = QuantileModel.load(PRODUCT_MODEL_DIR)
    assert tuple(model.features) == FEATURES
    frame = train.load_synthetic_dataset()
    start = train.PRODUCT_FOLDS[-1][0]
    assert model.metadata["trained_on_targets_before"] == start
    holdout = frame[frame["as_of"] >= start]
    X, y = holdout[list(FEATURES)].to_numpy(np.float64), holdout["y"].to_numpy()
    pred = model.predict_log_ratio(X)
    assert np.all(np.diff(pred, axis=1) >= 0), "quantiles cross"
    metrics = train.evaluate(y, pred, holdout["current_price"].to_numpy())
    record_result("shipped_product_weights_holdout_SYNTHETIC", metrics)
    print(f"\n[SYNTHETIC] shipped weights, holdout from {start}: {metrics}")
    assert metrics["coverage_p5_p95"] >= COVERAGE_FLOOR


def test_monotonicity_is_enforced_on_crossing_inputs():
    crossed = np.array([[0.02, -0.10, 0.0, -0.03, 0.01]])
    assert np.all(np.diff(enforce_monotonic(crossed), axis=1) >= 0)
    assert len(QUANTILE_NAMES) == 5


# ----------------------------------------------------------------------------- category index model (REAL)

def test_category_backtest_is_real_data(category_run):
    print(_summary("REAL", category_run))
    assert category_run["training_data_is_synthetic"] is False
    assert category_run["training_data"].startswith("REAL")


def test_category_pinball_loss_converges(category_run):
    for name, curve in category_run["validation_pinball_curves"].items():
        assert curve["best"] < curve["first_iteration"], name


@pytest.mark.parametrize("fold", range(N_FOLDS))
def test_category_pinball_beats_unconditional_quantiles_real(category_run, fold):
    rep = category_run["walk_forward_backtest"][fold]
    assert rep["model"]["pinball_loss_mean"] < rep["naive_unconditional_quantiles"]["pinball_loss_mean"]


@pytest.mark.parametrize("fold", range(N_FOLDS))
def test_category_p5_p95_coverage_real(category_run, fold):
    rep = category_run["walk_forward_backtest"][fold]
    assert rep["model"]["coverage_p5_p95"] >= COVERAGE_FLOOR, rep["test_period"]
