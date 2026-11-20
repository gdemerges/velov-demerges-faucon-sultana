"""Backtest du monitoring (velov.backtest) : briques de calibration, sans le rejeu complet (~30 s)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from velov import backtest
from velov.data import simulate
from velov.replay import apply_scenario


def test_judged_rows_have_known_truth():
    run_at = pd.Timestamp("2026-08-10T06:00Z")
    t = pd.date_range(pd.Timestamp("2026-08-06T00:00Z"), run_at, freq="h")
    preds = pd.DataFrame({"observed_at": t})
    judged = backtest.judged_at(preds, run_at, pd.Timedelta(days=3))
    assert judged["observed_at"].max() == run_at - pd.Timedelta(hours=1)  # la vérité de 5 h arrive à 6 h
    assert judged["observed_at"].min() > run_at - pd.Timedelta(days=3)


def test_bootstrap_interval_shrinks_with_more_predictions():
    rng = np.random.default_rng(0)
    em, ep = rng.gamma(2, 0.7, 168), rng.gamma(2, 1.0, 168)
    small = backtest.block_bootstrap_half_width(em, ep, 24, np.random.default_rng(1))
    large = backtest.block_bootstrap_half_width(em, ep, 166, np.random.default_rng(1))
    assert large < small


def test_calibration_uses_worst_station_and_caps_min_predictions():
    runs = pd.DataFrame({"run_at": range(100), "ratio": np.linspace(0.70, 0.80, 100)})
    # 20 stations par passage, la pire à 1,10 ou 1,20 : c'est elle que le seuil doit laisser passer.
    stations = pd.DataFrame(
        [{"run_at": r, "n": 70, "ratio": 0.9 + 0.01 * s} for r in range(100) for s in range(20)]
        + [{"run_at": r, "n": 70, "ratio": 1.2 if r == 99 else 1.1} for r in range(100)]
    )
    curve = pd.DataFrame({"n": [48, 96, 166], "half_width_median": [0.2, 0.12, 0.09]})
    th = backtest.calibrate(runs, stations, curve, pd.Timedelta(days=3), "test")
    assert th.station_min_predictions == 64  # 166 visé, plafonné à 90 % de 72 h
    assert th.ratio_max == 0.80
    assert 1.10 < th.station_ratio_max <= 1.20
    assert backtest.false_alarms(runs, stations, th)["false_alarm_any"] <= 0.01


def test_scenario_starts_at_given_date():
    raw = simulate(days=6, n_stations=3)
    raw["timestamp"] = pd.to_datetime(raw["timestamp"], utc=True)
    start = raw["timestamp"].min() + pd.Timedelta(days=3)
    cold = apply_scenario(raw, "froid", start=start)
    before = raw["timestamp"] < start
    assert (cold.loc[before, "temperature"] == raw.loc[before, "temperature"]).all()
    assert np.allclose(cold.loc[~before, "temperature"], raw.loc[~before, "temperature"] - 15, atol=0.11)
    moved = apply_scenario(raw, "demenagement", start=start)
    assert (moved.loc[before, "bikes_available"] == raw.loc[before, "bikes_available"]).all()
