"""Tests de modèle (S4) : EX-05 devient automatique, la reproductibilité et la règle de promotion sont testées.

Petit jeu simulé (30 jours, 5 stations, test sur 7 jours) : la CI reste rapide. Le seuil vérifié est
« mieux que la persistance », pas « aussi bon que le champion » : ce second contrôle se fait avec
velov.promote sur la vraie table.
"""

from __future__ import annotations

import json

import pytest

from velov.data import simulate
from velov.promote import MIN_GAIN, decide
from velov.train import train

WINDOW = {"start": "2026-08-16T00:00:00+00:00", "end": "2026-08-29T21:00:00+00:00", "n_rows": 6720}


@pytest.fixture(scope="module")
def history(tmp_path_factory):
    path = tmp_path_factory.mktemp("data") / "history.csv"
    simulate(days=30, n_stations=5, seed=1).to_csv(path, index=False)
    return path


@pytest.fixture(scope="module")
def trained(history, tmp_path_factory):
    out = tmp_path_factory.mktemp("model")
    return out, train(history, out, version="0.0.0-test", test_days=7, seed=42)


def test_model_beats_persistence_baseline(trained):
    _, metadata = trained
    m = metadata["metrics"]
    assert m["mae_model"] < m["mae_baseline_persistence"], m


def test_same_seed_gives_same_artifact(history, trained, tmp_path):
    _, first = trained
    second = train(history, tmp_path, version="0.0.0-test", test_days=7, seed=42)
    assert second["artifact"]["sha256"] == first["artifact"]["sha256"]
    assert second["metrics"] == first["metrics"]


def test_metadata_traces_params_and_data(trained):
    out, metadata = trained
    on_disk = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    assert on_disk["params"] == {
        "seed": 42,
        "test_days": 7,
        "learning_rate": 0.1,
        "max_iter": 200,
        "max_leaf_nodes": 31,
    }
    assert set(on_disk["metrics"]) >= {"mae_model", "mae_baseline_persistence", "mae_model_peak_hours"}
    data = on_disk["data"]
    assert len(data["sha256"]) == 64
    assert data["start"].startswith("2026-05-31T22:00:00")  # 1er juin 0 h à Lyon, en UTC
    assert data["n_rows"] == 30 * 24 * 5


def test_data_fingerprint_ignores_row_order(history, tmp_path):
    import pandas as pd

    from velov.train import load_frame

    shuffled = tmp_path / "shuffled.csv"
    pd.read_csv(history).sample(frac=1, random_state=0).to_csv(shuffled, index=False)
    assert load_frame(shuffled, None)[1]["sha256"] == load_frame(history, None)[1]["sha256"]


def test_training_refuses_naive_timestamps(tmp_path):
    path = tmp_path / "naive.csv"
    df = simulate(days=5, n_stations=2, seed=0)
    df.assign(timestamp=df["timestamp"].dt.tz_localize(None)).to_csv(path, index=False)
    with pytest.raises(ValueError, match="fuseau"):
        train(path, tmp_path / "out", test_days=1)


# --- Règle de promotion (velov.promote.decide), écrite avant de regarder les chiffres ---


def metrics(mae: float, baseline: float = 1.9, window: dict = WINDOW) -> dict:
    return {"mae_model": mae, "mae_baseline_persistence": baseline, "window": window}


def test_promotion_rule_first_champion_must_beat_persistence():
    assert decide(metrics(1.3), None)[0] is True
    promote, reason, _ = decide(metrics(2.0, baseline=1.9), None)
    assert promote is False and "persistance" in reason


def test_promotion_rule_requires_gain_over_champion():
    promote, _, target = decide(metrics(1.30), metrics(1.40))
    assert promote is True
    assert target == pytest.approx(1.40 * (1 - MIN_GAIN))


def test_promotion_rule_rejects_gain_within_seed_noise():
    # 1 % de mieux que le champion : de l'ordre de l'écart entre deux seeds, on garde le champion
    promote, reason, _ = decide(metrics(1.386), metrics(1.40))
    assert promote is False and "insuffisant" in reason


def test_promotion_rule_rejects_challenger_worse_than_persistence_even_if_better_than_champion():
    promote, reason, _ = decide(metrics(1.95, baseline=1.9), metrics(2.5))
    assert promote is False and "persistance" in reason


def test_promotion_rule_requires_same_window():
    other = {**WINDOW, "start": "2026-08-01T00:00:00+00:00"}
    promote, reason, _ = decide(metrics(1.0), metrics(1.4, window=other))
    assert promote is False and "fenêtres" in reason
