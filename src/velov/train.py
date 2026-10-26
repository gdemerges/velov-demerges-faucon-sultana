"""Entraînement du modèle v0 et production de l'artefact distribuable.

Ce qu'on distribue (S1) n'est pas "un modèle" mais un paquet :
- le pipeline sklearn complet (prétraitement + modèle) -> models/model.joblib
- ses métadonnées (version, features, métriques, empreinte SHA-256, versions des
  librairies, hash des données) -> models/metadata.json

Usage :
    python -m velov.train --data data/velov_history.csv --out models/
    python -m velov.train --data data/velov_history.csv --out models/ --mlflow
    DATABASE_URL=... python -m velov.train --from-gold --out models/    # table gold, contrat vérifié
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from velov.features import (
    CATEGORICAL_FEATURES,
    FEATURES,
    NUMERIC_FEATURES,
    RAW_COLUMNS,
    TARGET,
    add_features,
    make_training_frame,
)

MODEL_FILENAME = "model.joblib"
METADATA_FILENAME = "metadata.json"
SKOPS_TRUSTED_TYPES = ["sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor"]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_pipeline(seed: int = 42) -> Pipeline:
    preprocess = ColumnTransformer(
        [
            ("station", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
            ("num", "passthrough", NUMERIC_FEATURES),
        ]
    )
    model = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.1, random_state=seed)
    return Pipeline([("preprocess", preprocess), ("model", model)])


def temporal_split(df: pd.DataFrame, test_days: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Découpage temporel : on teste sur le futur, jamais sur un échantillon aléatoire."""
    cutoff = pd.to_datetime(df["timestamp"]).max() - pd.Timedelta(days=test_days)
    ts = pd.to_datetime(df["timestamp"])
    train, test = df[ts <= cutoff], df[ts > cutoff]
    if train.empty or test.empty:
        raise ValueError("Split vide : augmenter l'historique ou réduire test_days")
    return train, test


def load_frame(data_path: Path | None, gold_url: str | None) -> tuple[pd.DataFrame, str]:
    """Table d'entraînement et empreinte des données. Depuis gold, le contrat est vérifié d'abord (EX-12)."""
    if gold_url:
        from velov import contract

        gold = contract.validate(contract.read_gold(gold_url))  # lève SchemaErrors : rien n'est entraîné
        digest = hashlib.sha256(pd.util.hash_pandas_object(gold, index=False).to_numpy().tobytes()).hexdigest()
        return add_features(gold), digest
    if data_path is None or not data_path.exists():
        raise FileNotFoundError(f"{data_path} introuvable : lancer d'abord `python -m velov.data`")
    return make_training_frame(pd.read_csv(data_path, parse_dates=["timestamp"])), sha256_of(data_path)


def train(
    data_path: Path | None,
    out_dir: Path,
    version: str = "0.1.0",
    test_days: int = 14,
    seed: int = 42,
    gold_url: str | None = None,
) -> dict:
    """Entraîne, évalue contre une baseline, écrit l'artefact. Retourne les métadonnées."""
    frame, data_sha256 = load_frame(data_path, gold_url)
    train_df, test_df = temporal_split(frame, test_days)

    pipeline = build_pipeline(seed)
    pipeline.fit(train_df[FEATURES], train_df[TARGET])

    y_test = test_df[TARGET]
    pred = np.clip(pipeline.predict(test_df[FEATURES]), 0, test_df["capacity"])
    mae_model = float(mean_absolute_error(y_test, pred))
    # Baseline "persistance" : dans 1 h, il y aura autant de vélos que maintenant.
    # Le modèle doit justifier sa complexité face à cette baseline (exigence EX-05).
    mae_baseline = float(mean_absolute_error(y_test, test_df["bikes_available"]))

    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / MODEL_FILENAME
    joblib.dump(pipeline, model_path)

    metadata = {
        "model_name": "velov-availability",
        "model_version": version,
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "task": "Prédire le nombre de vélos disponibles à H+1 par station",
        "input_columns": RAW_COLUMNS,
        "features": FEATURES,
        "target": TARGET,
        "metrics": {
            "mae_model": round(mae_model, 3),
            "mae_baseline_persistence": round(mae_baseline, 3),
            "test_days": test_days,
            "n_train": int(len(train_df)),
            "n_test": int(len(test_df)),
        },
        "artifact": {"file": MODEL_FILENAME, "sha256": sha256_of(model_path)},
        "data_sha256": data_sha256,
        "data_source": "gold" if gold_url else "csv",
        "runtime": {
            "python": platform.python_version(),
            "scikit-learn": sklearn.__version__,
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
    }
    (out_dir / METADATA_FILENAME).write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return metadata


def log_to_mlflow(metadata: dict, out_dir: Path) -> None:
    """Journalise le run et le modèle dans MLflow (aperçu S1, approfondi en S4)."""
    import mlflow  # import local : MLflow n'est pas requis pour servir le modèle
    from mlflow.models import infer_signature

    pipeline = joblib.load(out_dir / MODEL_FILENAME)
    sample = make_training_frame(
        pd.DataFrame(
            {
                "station_id": [1, 1],
                "timestamp": pd.to_datetime(["2026-06-01T06:00Z", "2026-06-01T07:00Z"]),
                "capacity": [20, 20],
                "bikes_available": [8, 5],
                "temperature": [21.0, 22.0],
                "is_raining": [False, False],
            }
        )
    )[FEATURES]

    mlflow.set_experiment("velov-availability")
    with mlflow.start_run(run_name=f"v{metadata['model_version']}"):
        mlflow.log_params({"model": "HistGradientBoostingRegressor", "test_days": metadata["metrics"]["test_days"]})
        mlflow.log_metrics({k: v for k, v in metadata["metrics"].items() if k.startswith("mae")})
        mlflow.sklearn.log_model(
            pipeline,
            name="model",
            signature=infer_signature(sample, pipeline.predict(sample)),
            input_example=sample,
            # Depuis MLflow 3.x, sklearn est sérialisé par défaut en skops (format sûr,
            # sans exécution de code arbitraire), plus en pickle. skops refuse les types
            # qu'il ne connaît pas : on déclare explicitement ceux qu'on a vérifiés.
            serialization_format="skops",
            skops_trusted_types=SKOPS_TRUSTED_TYPES,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Entraîne le modèle Vélo'v v0.")
    parser.add_argument("--data", type=Path, default=Path("data/velov_history.csv"))
    parser.add_argument("--from-gold", action="store_true", help="lire gold.gold_training_hourly (DATABASE_URL)")
    parser.add_argument("--out", type=Path, default=Path("models"))
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--mlflow", action="store_true", help="journaliser aussi dans MLflow")
    args = parser.parse_args()

    gold_url = os.environ["DATABASE_URL"] if args.from_gold else None
    metadata = train(args.data, args.out, version=args.version, gold_url=gold_url)
    m = metadata["metrics"]
    print(f"MAE modèle   : {m['mae_model']:.3f} vélos")
    print(f"MAE baseline : {m['mae_baseline_persistence']:.3f} vélos (persistance)")
    print(f"Artefact     : {args.out / MODEL_FILENAME} (sha256 {metadata['artifact']['sha256'][:12]}...)")
    if args.mlflow:
        log_to_mlflow(metadata, args.out)
        print("Run journalisé dans MLflow (voir `mlflow ui`)")


if __name__ == "__main__":
    main()
