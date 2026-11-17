"""Artefact servi : noms de fichiers, empreinte, et entrées du graphe ONNX (ADR-0003).

Partagé par l'entraînement et l'API. Ce module n'importe PAS scikit-learn : l'image de serving
n'embarque que onnxruntime (scikit-learn et SciPy pèsent ~120 Mo et ne servaient qu'à charger le modèle).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import onnxruntime as ort
import pandas as pd

from velov.features import CATEGORICAL_FEATURES, NUMERIC_FEATURES

METADATA_FILENAME = "metadata.json"
ONNX_FILENAME = "model.onnx"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def onnx_feeds(X: pd.DataFrame) -> dict[str, np.ndarray]:
    """Une entrée ONNX par feature, dans le type déclaré à l'export : texte pour la station, float64 sinon.

    float64 et non float32 : les seuils de l'arbre sont comparés en double, comme dans scikit-learn
    (voir velov.train.use_exact_thresholds). Utilisée à l'export et par l'API : la même conversion des deux côtés.
    """
    feeds = {c: X[[c]].astype(str).to_numpy() for c in CATEGORICAL_FEATURES}
    feeds |= {c: X[[c]].astype(np.float64).to_numpy() for c in NUMERIC_FEATURES}
    return feeds


def onnx_session(path: Path, threads: int = 1) -> ort.InferenceSession:
    """Session ONNX Runtime avec un nombre de threads de calcul FIXÉ (0 = choix d'ORT : un par cœur vu).

    Dans un conteneur borné par --cpus, ORT voit quand même tous les cœurs de l'hôte (20 sur le poste du TP5)
    et lance autant de threads, qui attendent en boucle active : le quota CPU part en attente et le noyau
    bride le conteneur (cgroup nr_throttled). Une requête ne porte qu'une ligne : rien à paralléliser
    dedans, le parallélisme vient des workers (README, section Performance).
    """
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
