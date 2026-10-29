"""Suivi des entraînements dans MLflow (S4) : un run par entraînement, et le registre de modèles.

Chaque run répond aux quatre questions (S4 · 16) :
- quel code ?      tags git_commit, git_dirty
- quelles données ? tags data_sha256 (empreinte du contenu), data_start, data_end, et le jeu en entrée du run
- quels réglages ? paramètres seed, learning_rate, max_iter, max_leaf_nodes, test_days
- meilleur que quoi ? métriques de test mae_model, mae_baseline_persistence, mae_model_peak_hours

Aucune adresse en dur : le serveur vient de MLFLOW_TRACKING_URI (sqlite:///mlflow.db en local,
http://<ip-de-la-vm>:5000 sur le réseau de l'école). Module à part : train.py et l'API n'importent pas MLflow.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import mlflow
import pandas as pd
from mlflow import MlflowClient
from mlflow.models import infer_signature
from sklearn.pipeline import Pipeline

from velov.features import FEATURES, RAW_COLUMNS, TARGET
from velov.train import SKOPS_TRUSTED_TYPES

EXPERIMENT = "velov-availability"
MODEL_NAME = "velov-availability"
# Alias du registre (S4 · 21) : la dernière version enregistrée, celle qui sert, celle d'avant.
CHALLENGER, CHAMPION, PREVIOUS_CHAMPION = "challenger", "champion", "previous-champion"


def git_state(cwd: Path | None = None) -> dict[str, str]:
    """Commit courant et présence de modifications non commitées (un run git_dirty n'est pas rejouable)."""

    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    try:
        commit = git("rev-parse", "HEAD")
        dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    except (OSError, subprocess.CalledProcessError):
        return {"git_commit": "unknown", "git_dirty": "unknown"}
    return {"git_commit": commit, "git_dirty": str(dirty).lower()}


def log_training(
    metadata: dict,
    out_dir: Path,
    pipeline: Pipeline,
    frame: pd.DataFrame,
    register: bool = False,
) -> tuple[str, str | None]:
    """Trace un entraînement. Avec register, crée une version du registre et lui pose l'alias challenger.

    Retourne (run_id, version du registre ou None).
    """
    data = metadata["data"]
    metrics = {k: v for k, v in metadata["metrics"].items() if k.startswith("mae")}
    sample = frame[FEATURES].head(5)

    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run(run_name=f"v{metadata['model_version']}") as run:
        mlflow.log_params(metadata["params"])
        mlflow.log_metrics(metrics)
        mlflow.set_tags(
            {
                **git_state(),
                "model_version": metadata["model_version"],
                "data_source": data["source"],
                "data_sha256": data["sha256"],
                "data_start": data["start"],
                "data_end": data["end"],
            }
        )
        # Le jeu d'entraînement (schéma, nombre de lignes, empreinte) : pas les données elles-mêmes.
        dataset = mlflow.data.from_pandas(
            frame[RAW_COLUMNS + [TARGET]], name=data["source"], digest=data["sha256"][:16], targets=TARGET
        )
        mlflow.log_input(dataset, context="training")
        model_info = mlflow.sklearn.log_model(
            pipeline,
            name="model",
            signature=infer_signature(sample, pipeline.predict(sample)),
            input_example=sample,
            # Format skops (défaut MLflow 3) : pas d'exécution de code au chargement, contrairement à pickle.
            # skops refuse les types non déclarés : on ne liste que ceux qu'on a relus.
            serialization_format="skops",
            skops_trusted_types=SKOPS_TRUSTED_TYPES,
            registered_model_name=MODEL_NAME if register else None,
        )
        # Le paquet de S1 (ONNX servi + joblib + metadata.json), rangé avec le run qui l'a produit.
        mlflow.log_artifacts(str(out_dir), artifact_path="package")

    version = None
    if register:
        version = str(model_info.registered_model_version)
        client = MlflowClient()
        client.set_registered_model_alias(MODEL_NAME, CHALLENGER, version)
        for key, value in {**metrics, "git_commit": git_state()["git_commit"], "data_sha256": data["sha256"]}.items():
            client.set_model_version_tag(MODEL_NAME, version, key, str(value))
    return run.info.run_id, version
