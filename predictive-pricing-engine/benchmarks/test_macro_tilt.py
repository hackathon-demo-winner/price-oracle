"""The Gemini macro heuristic: a small, capped shift of the forecast (no retraining)."""
from ml_engine.serving import MACRO_TILT_CAP, handle_forecast, macro_tilt


class FixedMacro:
    def __init__(self, signals):
        self.signals = signals

    def get(self):
        if self.signals is None:
            return None
        return {"source": "test", "signals": self.signals, "fetchedAt": "2026-10-11T00:00:00Z", "usedByModel": False}


BODY = {"currentPrice": 249.69, "regularPrice": None, "category": None, "retailer": "bestbuy", "title": "Apple - AirPods Pro 2"}


def mid(signals):
    status, payload = handle_forecast("POST", BODY, macro=FixedMacro(signals))
    assert status == 200
    return payload["forecast"]


def test_no_signals_means_no_shift():
    assert macro_tilt(None) == 0.0
    assert macro_tilt({}) == 0.0
    assert macro_tilt({"recession_probability": None, "gdp_growth_above_2pct_probability": None}) == 0.0


def test_direction_of_each_signal():
    assert macro_tilt({"fed_hike_probability_next_meeting": 0.6}) > 0
    assert macro_tilt({"gdp_growth_above_2pct_probability": 0.9}) > 0
    assert macro_tilt({"fed_cut_probability_next_meeting": 0.6}) < 0
    assert macro_tilt({"recession_probability": 0.6}) < 0
    assert macro_tilt({"unemployment_above_5pct_probability": 0.6}) < 0


def test_shift_is_capped():
    hot = {"fed_hike_probability_next_meeting": 1.0, "gdp_growth_above_2pct_probability": 1.0}
    cold = {"fed_cut_probability_next_meeting": 1.0, "recession_probability": 1.0, "unemployment_above_5pct_probability": 1.0, "gdp_growth_above_2pct_probability": 0.0}
    assert 0 < macro_tilt(hot) <= MACRO_TILT_CAP
    assert -MACRO_TILT_CAP <= macro_tilt(cold) < 0
    assert macro_tilt(cold) == -MACRO_TILT_CAP


def test_forecast_moves_slightly_and_reports_the_adjustment():
    neutral = mid(None)
    hot = mid({"fed_hike_probability_next_meeting": 1.0, "gdp_growth_above_2pct_probability": 1.0})
    cold = mid({"recession_probability": 1.0, "unemployment_above_5pct_probability": 1.0, "fed_cut_probability_next_meeting": 1.0})

    assert neutral["macro"] is None
    assert cold["projectedMid"] < neutral["projectedMid"] < hot["projectedMid"]
    for shifted in (hot, cold):
        assert abs(shifted["projectedMid"] / neutral["projectedMid"] - 1) <= MACRO_TILT_CAP + 1e-4
        q = shifted["quantiles"]
        assert q["lowest"] <= q["low"] <= q["mid"] <= q["high"] <= q["highest"]
    assert hot["macro"]["heuristicAdjustmentPercent"] > 0 > cold["macro"]["heuristicAdjustmentPercent"]
    assert hot["macro"]["usedByModel"] is False
