"""Serving ONNX (ADR-0003) : le modèle servi prédit comme celui évalué, et l'API n'a pas besoin de scikit-learn."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import numpy as np
from fastapi.testclient import TestClient

from velov.artifact import ONNX_FILENAME
from velov.train import ONNX_MAX_ABS_DIFF


def test_artifact_is_onnx_and_faithful(model_dir):
    meta = json.loads((model_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["artifact"]["file"] == ONNX_FILENAME
    assert meta["artifact"]["format"] == "onnx"
    # l'export a été comparé à scikit-learn sur le jeu de test (l'entraînement échoue au-delà du seuil)
    assert meta["artifact"]["max_abs_diff_vs_sklearn"] <= ONNX_MAX_ABS_DIFF


def test_serving_does_not_import_sklearn():
    # Garantit le gain de taille de l'image : ni l'API ni le collecteur ne chargent scikit-learn ou SciPy.
    code = (
        "import sys, velov.api.main, velov.ingest; "
        "bad = sorted({m.split('.')[0] for m in sys.modules} & {'sklearn', 'scipy', 'joblib', 'skl2onnx'}); "
        "sys.exit(f'importés : {bad}' if bad else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)  # noqa: S603
    assert result.returncode == 0, result.stderr


def test_tampered_onnx_is_refused(model_dir, tmp_path, monkeypatch):
    tampered = tmp_path / "models"
    shutil.copytree(model_dir, tampered)
    with (tampered / ONNX_FILENAME).open("ab") as f:
        f.write(b"\x00")  # un octet de plus : l'empreinte SHA-256 ne correspond plus
    monkeypatch.setenv("MODEL_DIR", str(tampered))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from velov.api.main import app

    with TestClient(app) as c:
        assert c.get("/ready").status_code == 503


def test_export_exact_when_value_falls_on_threshold(tmp_path):
    # Régression (train --from-gold) : scikit-learn place ses seuils au milieu de deux valeurs vues
    # (26,7 et 26,9 -> 26,799999999999997). Une valeur jamais vue, 26,8, tombe juste au-dessus en float64
    # mais devient ÉGALE au seuil en float32 : un arbre ONNX en float32 l'envoyait de l'autre côté.
    from velov.data import simulate
    from velov.features import FEATURES, TARGET, make_training_frame
    from velov.train import build_pipeline, export_onnx

    frame = make_training_frame(simulate(days=10, n_stations=2, seed=0))
    temps = np.where(np.arange(len(frame)) % 2 == 0, 26.7, 26.9)
    frame = frame.assign(temperature=temps, **{TARGET: np.where(temps > 26.8, 10.0, 0.0)})
    pipeline = build_pipeline().fit(frame[FEATURES], frame[TARGET])
    X_check = frame[FEATURES].head(20).assign(temperature=26.8)
    assert pipeline.predict(X_check).min() > 5  # scikit-learn : 26,8 > seuil, branche "chaude"
    assert export_onnx(pipeline, tmp_path / "m.onnx", X_check) <= ONNX_MAX_ABS_DIFF
