"""Tests de velov.monitor (EX-21) : fenêtres, vérité à H+1, MAE face à la persistance, alertes."""

from __future__ import annotations

import pandas as pd
import pytest

from velov import monitor
from velov.train import load_table


def _rows(n_hours: int = 3, stations=(1, 2), version: str = "1.0.0") -> pd.DataFrame:
    """Lignes comme la table predictions : la station voit 10, 11, 12... vélos, le modèle prédit +1."""
    t0 = pd.Timestamp("2026-11-01 08:00", tz="UTC")
    rows = []
    for s in stations:
        for h in range(n_hours):
            rows.append(
                {
                    "station_id": s,
                    "observed_at": t0 + pd.Timedelta(hours=h),
                    "capacity": 20,
                    "bikes_available": 10 + h,
                    "temperature": 15.0,
                    "is_raining": False,
                    "predicted_bikes": 11.0 + h,
                    "model_version": version,
                    "created_at": pd.Timestamp("2026-11-02", tz="UTC"),
                }
            )
    return pd.DataFrame(rows)


def test_truth_is_next_hour_observation():
    current = monitor.current_window(_rows(), "1.0.0")
    first = current[(current["station_id"] == 1)].iloc[0]
    assert first["bikes_next_hour"] == 11  # observé à t + 1 h
    assert current["bikes_next_hour"].isna().sum() == 2  # dernière heure de chaque station : pas encore de vérité


def test_latest_prediction_wins_when_hour_replayed():
    rows = _rows()
    replay = rows.iloc[[0]].assign(predicted_bikes=99.0, created_at=pd.Timestamp("2026-11-03", tz="UTC"))
    current = monitor.current_window(pd.concat([rows, replay]), "1.0.0")
    assert len(current) == len(rows)
    assert current.iloc[0]["predicted_bikes"] == 99.0


def test_other_versions_excluded_but_still_give_truth():
    rows = _rows()
    rows.loc[rows["observed_at"] == rows["observed_at"].max(), "model_version"] = "0.9.0"
    current = monitor.current_window(rows, "1.0.0")
    assert set(current["model_version"]) == {"1.0.0"}
    assert current["bikes_next_hour"].notna().all()  # l'heure servie par 0.9.0 sert de vérité


def test_performance_against_persistence():
    current = monitor.current_window(_rows(), "1.0.0")
    overall, by_station = monitor.performance(current)
    # Le modèle prédit exactement l'heure suivante, la persistance se trompe d'un vélo.
    assert overall == {"n": 4, "mae_model": 0.0, "mae_persistence": 1.0, "ratio": 0.0}
    assert list(by_station.index) == [1, 2]


def test_alerts_global_ratio_and_station_worse_than_persistence():
    by_station = pd.DataFrame(
        {"n": [200, 200, 10], "mae_model": [2.85, 1.0, 5.0], "mae_persistence": [2.79, 2.0, 1.0]},
        index=pd.Index([17, 3, 9], name="station_id"),
    ).assign(ratio=lambda d: d["mae_model"] / d["mae_persistence"])
    assert monitor.alerts({"ratio": 0.87}, by_station) == [
        "station 17 : MAE modèle 2.85 > persistance 2.79 (ratio 1.02)"
    ]  # station 9 : trop peu de prédictions pour conclure
    assert monitor.alerts({"ratio": 0.95}, by_station.iloc[[1]]) == ["ratio global 0.95 > 0.90"]


def test_reference_is_first_week_of_test_window(model_dir):
    meta = monitor.load_metadata(model_dir)
    raw = load_table(model_dir.parent / "history.csv")
    window = monitor.reference_window(raw, meta, days=1)
    assert window["timestamp"].min() > pd.Timestamp(meta["data"]["end"]) - pd.Timedelta(days=3)
    assert window["timestamp"].nunique() == 24
    reference = monitor.predict_reference(window, model_dir)
    assert set(monitor.DRIFT_COLUMNS) <= set(reference.columns)
    assert (reference["predicted_bikes"] >= 0).all()


def test_drift_scores_one_per_column(model_dir, tmp_path):
    raw = load_table(model_dir.parent / "history.csv")
    meta = monitor.load_metadata(model_dir)
    reference = monitor.predict_reference(monitor.reference_window(raw, meta, days=3), model_dir)
    # Au-delà de 1000 lignes, Evidently mesure une distance de Wasserstein normée (en dessous : une p-value K-S).
    reference = pd.concat([reference] * 5, ignore_index=True)
    current = reference.assign(temperature=reference["temperature"] - 15)
    scores = monitor.drift(reference, current, tmp_path / "drift.html")
    assert list(scores) == monitor.DRIFT_COLUMNS
    assert scores["temperature"] > 0.1 >= scores["bikes_available"]
    assert (tmp_path / "drift.html").exists()


@pytest.mark.parametrize("url", ["postgresql+psycopg://u:p@127.0.0.1:1/x"])
def test_sqlalchemy_url_accepted(url, monkeypatch):
    seen = {}

    class _Stop(Exception):
        pass

    def fake_connect(u, **_):
        seen["url"] = u
        raise _Stop

    import psycopg

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    with pytest.raises(_Stop):
        monitor.fetch_predictions(url, 7)
    assert seen["url"].startswith("postgresql://")


def test_no_truth_means_nothing_to_judge():
    """Une seule heure par station : aucune vérité à H+1, donc rien à juger (main() sort en code 2)."""
    overall, by_station = monitor.performance(monitor.current_window(_rows(n_hours=1), "1.0.0"))
    assert overall["n"] == 0 and by_station.empty


def test_window_ignores_future_observations():
    assert "observed_at <= now()" in monitor.CURRENT_SQL
