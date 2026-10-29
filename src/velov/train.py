"""Entraînement du modèle v0 et production de l'artefact distribuable.

Ce qu'on distribue (S1) n'est pas "un modèle" mais un paquet :
- le pipeline complet (prétraitement + modèle) exporté en ONNX -> models/model.onnx (servi par l'API, ADR-0003)
- le même pipeline scikit-learn -> models/model.joblib (MLflow, analyse ; jamais dans l'image)
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
import importlib.metadata
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

from velov.artifact import METADATA_FILENAME, ONNX_FILENAME, onnx_feeds, sha256_of
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
SKOPS_TRUSTED_TYPES = ["sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor"]
# Écart maximal toléré entre ONNX et scikit-learn, en vélos. Mesuré : ~1e-6 (feuilles sommées en float32).
ONNX_MAX_ABS_DIFF = 1e-3
ONNX_OPSET = {"": 17, "ai.onnx.ml": 3}


def build_pipeline(seed: int = 42) -> Pipeline:
    preprocess = ColumnTransformer(
        [
            ("station", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
            ("num", "passthrough", NUMERIC_FEATURES),
        ]
    )
    model = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.1, random_state=seed)
    return Pipeline([("preprocess", preprocess), ("model", model)])


def use_exact_thresholds(onx, pipeline: Pipeline) -> None:
    """Remplace les seuils float32 de l'arbre ONNX par les seuils float64 exacts de scikit-learn.

    skl2onnx 1.20 écrit les seuils du HistGradientBoosting en float32. Or scikit-learn les place au milieu
    de deux valeurs vues (26,7 et 26,9 -> 26,799999999999997) : une valeur jamais vue (26,8) est juste
    au-dessus en float64 mais égale au seuil en float32, et part dans l'autre branche (écart de 0,4 vélo
    constaté sur la table gold). nodes_values_as_tensor (ai.onnx.ml >= 3) garde des seuils float64 ;
    onnxruntime compare alors en double, comme scikit-learn. La sortie de l'arbre reste en float32.
    """
    import onnx
    from onnx import numpy_helper

    (tree,) = [n for n in onx.graph.node if n.op_type == "TreeEnsembleRegressor"]
    attrs = {a.name: a for a in tree.attribute}
    modes = onnx.helper.get_attribute_value(attrs["nodes_modes"])
    values_f32 = onnx.helper.get_attribute_value(attrs["nodes_values"])
    # Même ordre de parcours que le convertisseur : arbres puis nœuds, feuilles exclues.
    exact = iter(
        float(node["num_threshold"])
        for predictors in pipeline.named_steps["model"]._predictors
        for predictor in predictors
        for node in predictor.nodes
        if not node["is_leaf"]
    )
    values = []
    for mode, value in zip(modes, values_f32, strict=True):
        threshold = 0.0 if mode == b"LEAF" else next(exact)
        if np.float32(threshold) != np.float32(value):  # garde-fou : l'ordre des nœuds a changé
            raise RuntimeError("Seuils ONNX et scikit-learn désalignés : vérifier la version de skl2onnx")
        values.append(threshold)
    if next(exact, None) is not None:
        raise RuntimeError("Seuils ONNX et scikit-learn désalignés : nombre de nœuds différent")

    tree.attribute.remove(attrs["nodes_values"])
    exact_tensor = numpy_helper.from_array(np.array(values, dtype=np.float64))
    tree.attribute.append(onnx.helper.make_attribute("nodes_values_as_tensor", exact_tensor))
    for opset in onx.opset_import:
        if opset.domain == "ai.onnx.ml":
            opset.version = max(opset.version, 3)  # skl2onnx déclare 1, l'attribut tensor exige 3
    # onnxruntime renvoie du float32 pour TreeEnsembleRegressor, même en entrée float64.
    (output,) = [o for o in onx.graph.output if o.name == tree.output[0]]
    output.type.tensor_type.elem_type = onnx.TensorProto.FLOAT
    onnx.checker.check_model(onx)


def export_onnx(pipeline: Pipeline, path: Path, X_check: pd.DataFrame) -> float:
    """Exporte le pipeline en ONNX et vérifie qu'il prédit comme scikit-learn. Retourne l'écart maximal.

    Raises:
        RuntimeError: si l'écart dépasse ONNX_MAX_ABS_DIFF : le modèle servi doit être celui qui a été évalué.
    """
    import onnxruntime as ort
    from skl2onnx import to_onnx
    from skl2onnx.common.data_types import DoubleTensorType, StringTensorType

    initial_types = [(c, StringTensorType([None, 1])) for c in CATEGORICAL_FEATURES]
    initial_types += [(c, DoubleTensorType([None, 1])) for c in NUMERIC_FEATURES]
    onx = to_onnx(pipeline, initial_types=initial_types, target_opset=ONNX_OPSET)
    use_exact_thresholds(onx, pipeline)
    path.write_bytes(onx.SerializeToString())

    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    pred_onnx = session.run(None, onnx_feeds(X_check))[0].ravel()
    max_diff = float(np.abs(pred_onnx - pipeline.predict(X_check)).max())
    if max_diff > ONNX_MAX_ABS_DIFF:
        raise RuntimeError(f"Export ONNX infidèle : écart max {max_diff:.2e} vélo > {ONNX_MAX_ABS_DIFF}")
    return max_diff


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
    joblib.dump(pipeline, out_dir / MODEL_FILENAME)
    onnx_path = out_dir / ONNX_FILENAME
    onnx_max_diff = export_onnx(pipeline, onnx_path, test_df[FEATURES])

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
        "artifact": {
            "file": ONNX_FILENAME,
            "format": "onnx",
            "sha256": sha256_of(onnx_path),
            "opset": ONNX_OPSET,
            "max_abs_diff_vs_sklearn": onnx_max_diff,
        },
        "data_sha256": data_sha256,
        "data_source": "gold" if gold_url else "csv",
        "runtime": {
            "python": platform.python_version(),
            "scikit-learn": sklearn.__version__,
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "skl2onnx": importlib.metadata.version("skl2onnx"),
            "onnxruntime": importlib.metadata.version("onnxruntime"),
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
    print(f"Artefact     : {args.out / ONNX_FILENAME} (sha256 {metadata['artifact']['sha256'][:12]}..., "
          f"écart ONNX / sklearn {metadata['artifact']['max_abs_diff_vs_sklearn']:.1e} vélo)")
    if args.mlflow:
        log_to_mlflow(metadata, args.out)
        print("Run journalisé dans MLflow (voir `mlflow ui`)")


if __name__ == "__main__":
    main()
