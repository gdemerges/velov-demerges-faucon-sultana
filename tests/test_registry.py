"""Registre MLflow de bout en bout, sur un MLflow local (sqlite) : run tracé, challenger, promotion,
export vers l'API, retour arrière (EX-13, EX-14)."""

from __future__ import annotations

import json

import pytest

from velov.data import simulate

mlflow = pytest.importorskip("mlflow")


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """MLflow isolé dans tmp_path : base sqlite et artefacts (./mlruns) hors du dépôt."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_path / 'mlflow.db'}")
    monkeypatch.setenv("MLFLOW_DISABLE_TELEMETRY", "true")
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    path = tmp_path / "history.csv"
    simulate(days=20, n_stations=3, seed=0).to_csv(path, index=False)
    yield path
    mlflow.set_tracking_uri(None)


def train_and_register(history, out, seed=42, **params):
    from velov.tracking import log_training
    from velov.train import load_frame, train_frame

    frame, info = load_frame(history, None)
    metadata, pipeline = train_frame(frame, info, out, test_days=3, seed=seed, params=params)
    return metadata, *log_training(metadata, out, pipeline, frame, register=True)


def test_run_answers_the_four_questions(registry, tmp_path):
    metadata, run_id, version = train_and_register(registry, tmp_path / "m1")
    run = mlflow.get_run(run_id)
    assert run.data.params["seed"] == "42" and run.data.params["test_days"] == "3"
    assert {"mae_model", "mae_baseline_persistence", "mae_model_peak_hours"} <= set(run.data.metrics)
    assert {"git_commit", "git_dirty", "data_sha256", "data_start", "data_end"} <= set(run.data.tags)
    assert run.data.tags["data_sha256"] == metadata["data"]["sha256"]
    assert version == "1"


def test_promote_apply_export_and_rollback(registry, tmp_path):
    from mlflow import MlflowClient

    from velov.export import export
    from velov.promote import apply, compare, rollback
    from velov.tracking import CHAMPION, MODEL_NAME, PREVIOUS_CHAMPION
    from velov.train import load_frame, temporal_split

    client = MlflowClient()
    _, test_df = temporal_split(load_frame(registry, None)[0], 3)

    train_and_register(registry, tmp_path / "m1")
    first = compare(test_df, client)
    assert first["promote"] and "premier champion" in first["reason"]
    apply(first, client)
    assert str(client.get_model_version_by_alias(MODEL_NAME, CHAMPION).version) == "1"

    # Un modèle volontairement dégradé : enregistré comme challenger, jamais promu.
    train_and_register(registry, tmp_path / "m2", learning_rate=3.0)
    second = compare(test_df, client)
    assert not second["promote"]
    apply(second, client)
    assert str(client.get_model_version_by_alias(MODEL_NAME, CHAMPION).version) == "1"
    assert "promotion_refusee" in client.get_model_version(MODEL_NAME, "2").tags

    # Livraison : le paquet exporté porte la version du registre, et l'API la renvoie (EX-13).
    registry_block = export(CHAMPION, tmp_path / "served")
    served = json.loads((tmp_path / "served" / "metadata.json").read_text(encoding="utf-8"))
    assert served["registry"]["version"] == "1" == registry_block["version"]
    assert not (tmp_path / "served" / "model.joblib").exists()

    # Retour arrière : on force une promotion de v2, puis on revient à v1.
    client.set_registered_model_alias(MODEL_NAME, PREVIOUS_CHAMPION, "1")
    client.set_registered_model_alias(MODEL_NAME, CHAMPION, "2")
    assert rollback(client) == ("1", "2")
    assert str(client.get_model_version_by_alias(MODEL_NAME, CHAMPION).version) == "1"
    assert str(client.get_model_version_by_alias(MODEL_NAME, PREVIOUS_CHAMPION).version) == "2"


def test_api_exposes_registry_version(registry, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from velov.export import export
    from velov.promote import apply, compare
    from velov.train import load_frame, temporal_split

    _, run_id, _ = train_and_register(registry, tmp_path / "m1")
    _, test_df = temporal_split(load_frame(registry, None)[0], 3)
    from mlflow import MlflowClient

    apply(compare(test_df, MlflowClient()), MlflowClient())
    export("champion", tmp_path / "served")

    monkeypatch.setenv("MODEL_DIR", str(tmp_path / "served"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from velov.api.main import app

    with TestClient(app) as c:
        body = c.get("/v1/model").json()
    assert body["registry"]["run_id"] == run_id
    assert body["registry"]["alias"] == "champion"
    assert len(body["data"]["sha256"]) == 64
