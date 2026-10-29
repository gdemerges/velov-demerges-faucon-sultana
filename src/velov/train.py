"""Entraînement du modèle et production de l'artefact distribuable.

Ce qu'on distribue (S1) n'est pas "un modèle" mais un paquet :
- le pipeline complet (prétraitement + modèle) exporté en ONNX -> models/model.onnx (servi par l'API, ADR-0003)
- le même pipeline scikit-learn -> models/model.joblib (MLflow, analyse ; jamais dans l'image)
- ses métadonnées (version, paramètres, métriques, empreinte SHA-256, versions des
  librairies, empreinte et période des données) -> models/metadata.json

Ce module n'importe pas MLflow : le suivi des runs est dans velov.tracking (S4).

Usage :
    python -m velov.train --data data/velov_history.csv --out models/ --seed 42
    python -m velov.train --mlflow                      # + run MLflow (MLFLOW_TRACKING_URI)
    python -m velov.train --mlflow --register           # + version du registre, alias challenger
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
# Hyperparamètres par défaut, tracés avec chaque run (S4) : un changement ici doit se voir dans MLflow.
DEFAULT_PARAMS = {"learning_rate": 0.1, "max_iter": 200, "max_leaf_nodes": 31}
# Heures de pointe, heure de Lyon : 7 h-9 h et 17 h-19 h. C'est là que les stations se vident et se remplissent.
PEAK_HOURS = (7, 8, 17, 18)


def build_pipeline(seed: int = 42, **params) -> Pipeline:
    preprocess = ColumnTransformer(
        [
            ("station", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
            ("num", "passthrough", NUMERIC_FEATURES),
        ]
    )
    model = HistGradientBoostingRegressor(**(DEFAULT_PARAMS | params), random_state=seed)
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


def evaluate(model, test_df: pd.DataFrame) -> dict[str, float]:
    """MAE du modèle (globale et heures de pointe) et de la persistance, sur une fenêtre de test.

    Partagée par l'entraînement et velov.promote : challenger et champion sont mesurés par la même fonction.
    """
    y = test_df[TARGET]
    pred = np.clip(model.predict(test_df[FEATURES]), 0, test_df["capacity"])
    peak = test_df["hour"].isin(PEAK_HOURS).to_numpy()
    return {
        "mae_model": round(float(mean_absolute_error(y, pred)), 3),
        # Baseline "persistance" : dans 1 h, il y aura autant de vélos que maintenant.
        # Le modèle doit justifier sa complexité face à cette baseline (exigence EX-05).
        "mae_baseline_persistence": round(float(mean_absolute_error(y, test_df["bikes_available"])), 3),
        "mae_model_peak_hours": round(float(mean_absolute_error(y[peak], pred[peak])), 3),
    }


def data_fingerprint(df: pd.DataFrame) -> str:
    """SHA-256 du CONTENU de la table, lignes triées par (station_id, timestamp).

    On hache le contenu et non le fichier : le SHA-256 d'un fichier change dès qu'il est réécrit,
    même à données égales. Dépend de la version de pandas : sert à comparer des runs d'un même environnement.
    """
    rows = df.sort_values(["station_id", "timestamp"]).reset_index(drop=True)
    return hashlib.sha256(pd.util.hash_pandas_object(rows, index=False).to_numpy().tobytes()).hexdigest()


def load_frame(data_path: Path | None, gold_url: str | None) -> tuple[pd.DataFrame, dict]:
    """Table d'entraînement et description des données (source, empreinte, période UTC).

    Depuis gold, le contrat est vérifié d'abord (EX-12).
    """
    if gold_url:
        from velov import contract

        raw = contract.validate(contract.read_gold(gold_url))  # lève SchemaErrors : rien n'est entraîné
        frame, source = add_features(raw), contract.GOLD_TABLE
    else:
        if data_path is None or not data_path.exists():
            raise FileNotFoundError(f"{data_path} introuvable : lancer d'abord `python -m velov.data`")
        raw = pd.read_csv(data_path, parse_dates=["timestamp"])
        frame, source = make_training_frame(raw), data_path.name
    ts = pd.to_datetime(raw["timestamp"]).dt.tz_convert("UTC")
    info = {
        "source": source,
        "sha256": data_fingerprint(raw),
        "start": ts.min().isoformat(),
        "end": ts.max().isoformat(),
        "n_rows": int(len(raw)),
    }
    return frame, info


def train_frame(
    frame: pd.DataFrame,
    data_info: dict,
    out_dir: Path,
    version: str = "0.1.0",
    test_days: int = 14,
    seed: int = 42,
    params: dict | None = None,
) -> tuple[dict, Pipeline]:
    """Entraîne, évalue contre la baseline, écrit le paquet. Retourne les métadonnées et le pipeline."""
    params = DEFAULT_PARAMS | (params or {})
    train_df, test_df = temporal_split(frame, test_days)

    pipeline = build_pipeline(seed, **params)
    pipeline.fit(train_df[FEATURES], train_df[TARGET])
    maes = evaluate(pipeline, test_df)

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
        "params": {"seed": seed, "test_days": test_days, **params},
        "metrics": {
            **maes,
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
        "data": data_info,
        "data_sha256": data_info["sha256"],
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
    return metadata, pipeline


def train(
    data_path: Path | None,
    out_dir: Path,
    version: str = "0.1.0",
    test_days: int = 14,
    seed: int = 42,
    gold_url: str | None = None,
    params: dict | None = None,
) -> dict:
    """Charge les données, entraîne, écrit l'artefact. Retourne les métadonnées."""
    frame, data_info = load_frame(data_path, gold_url)
    metadata, _ = train_frame(frame, data_info, out_dir, version, test_days, seed, params)
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Entraîne le modèle Vélo'v.")
    parser.add_argument("--data", type=Path, default=Path("data/velov_history.csv"))
    parser.add_argument("--from-gold", action="store_true", help="lire gold.gold_training_hourly (DATABASE_URL)")
    parser.add_argument("--out", type=Path, default=Path("models"))
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--test-days", type=int, default=14)
    parser.add_argument("--learning-rate", type=float, default=DEFAULT_PARAMS["learning_rate"])
    parser.add_argument("--max-iter", type=int, default=DEFAULT_PARAMS["max_iter"])
    parser.add_argument("--mlflow", action="store_true", help="tracer le run dans MLflow (MLFLOW_TRACKING_URI)")
    parser.add_argument("--register", action="store_true", help="avec --mlflow : nouvelle version, alias challenger")
    args = parser.parse_args()

    gold_url = os.environ["DATABASE_URL"] if args.from_gold else None
    frame, data_info = load_frame(args.data, gold_url)
    params = {"learning_rate": args.learning_rate, "max_iter": args.max_iter}
    metadata, pipeline = train_frame(frame, data_info, args.out, args.version, args.test_days, args.seed, params)
    m = metadata["metrics"]
    print(f"MAE modèle   : {m['mae_model']:.3f} vélos (heures de pointe : {m['mae_model_peak_hours']:.3f})")
    print(f"MAE baseline : {m['mae_baseline_persistence']:.3f} vélos (persistance)")
    print(
        f"Données      : {data_info['source']}, {data_info['start']} -> {data_info['end']}, "
        f"empreinte {data_info['sha256'][:12]}"
    )
    print(
        f"Artefact     : {args.out / ONNX_FILENAME} (sha256 {metadata['artifact']['sha256'][:12]}..., "
        f"écart ONNX / sklearn {metadata['artifact']['max_abs_diff_vs_sklearn']:.1e} vélo)"
    )
    if args.mlflow:
        from velov import tracking  # import local : MLflow n'est pas requis pour entraîner ni servir

        run_id, version = tracking.log_training(metadata, args.out, pipeline, frame, register=args.register)
        print(f"Run MLflow   : {run_id}" + (f", version {version} du registre (@challenger)" if version else ""))
    elif args.register:
        parser.error("--register demande --mlflow")


if __name__ == "__main__":
    main()
