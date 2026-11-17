"""API de serving du modèle Vélo'v.

TP1, partie 3 : exposez le modèle. Mode : IA déclarée autorisée pour cette partie.

Endpoints attendus (niveaux du TP1 : Must, Should, Stretch) :
    POST /v1/predict        [Must]    une prédiction
    POST /v1/predict/batch  [Stretch] jusqu'à 1000 prédictions en un appel
    GET  /health            [Should]  liveness : le process répond (ne dépend pas du modèle)
    GET  /ready             [Should]  readiness : 200 si le modèle est chargé, 503 sinon
    GET  /v1/model          [S4]      quel modèle tourne : version du registre, run, données (EX-13)

Chaque réponse porte l'en-tête X-Process-Time-Ms : le temps passé DANS l'API (S1, mesuré au TP5).

Lancement :
    uvicorn velov.api.main:app --reload
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import numpy as np
import onnxruntime as ort
import pandas as pd
from fastapi import FastAPI, HTTPException, Request

from velov import db
from velov.api.schemas import (
    BatchPredictionRequest,
    BatchPredictionResponse,
    PredictionRequest,
    PredictionResponse,
)
from velov.artifact import METADATA_FILENAME, onnx_feeds, sha256_of
from velov.features import FEATURES, add_features

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("velov.api")

STATE: dict = {"model": None, "metadata": None}


def load_model(model_dir: Path) -> tuple[object, dict]:
    """Charge le modèle ONNX APRÈS avoir vérifié son empreinte SHA-256 (ADR-0001, ADR-0003)."""
    metadata_path = model_dir / METADATA_FILENAME
    if not metadata_path.exists():
        raise FileNotFoundError(f"{metadata_path} introuvable")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    model_path = model_dir / metadata["artifact"]["file"]
    if sha256_of(model_path) != metadata["artifact"]["sha256"]:
        raise RuntimeError(f"Empreinte invalide pour {model_path}")
    # ONNX : un graphe de calcul, aucun code exécuté au chargement (contrairement à pickle / joblib).
    return ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"]), metadata


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Fourni : exécuté une fois au démarrage (avant yield) et à l'arrêt (après yield)."""
    model_dir = Path(os.getenv("MODEL_DIR", "models"))
    try:
        STATE["model"], STATE["metadata"] = load_model(model_dir)
        logger.info("Modèle %s chargé", STATE["metadata"]["model_version"])
    except Exception:
        logger.exception("Échec du chargement du modèle depuis %s", model_dir)
    url = db.database_url()
    if url:
        try:
            db.init_db(url)
        except Exception:
            logger.exception("Base de données indisponible : les prédictions ne seront pas enregistrées")
    yield
    STATE.update(model=None, metadata=None)


app = FastAPI(title="Vélo'v availability API", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def add_process_time(request: Request, call_next):
    """Temps passé dans l'API, sans le réseau ni l'attente avant d'être servi (comparé à Locust au TP5)."""
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - start) * 1000:.2f}"
    return response


@app.get("/health")
def health() -> dict:
    """Liveness : le process répond, sans dépendre du modèle."""
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict:
    """Readiness : 200 seulement si le modèle est chargé."""
    if STATE["model"] is None:
        raise HTTPException(status_code=503, detail="Modèle non chargé")
    return {"status": "ready", "model_version": STATE["metadata"]["model_version"]}


@app.get("/v1/model")
def model_info() -> dict:
    """Quel modèle tourne (EX-13) : version, réglages, métriques, données, et version du registre si exporté."""
    if STATE["model"] is None:
        raise HTTPException(status_code=503, detail="Modèle non chargé")
    meta = STATE["metadata"]
    keys = ("model_name", "model_version", "trained_at", "params", "metrics", "data", "registry")
    return {k: meta.get(k) for k in keys} | {"artifact_sha256": meta["artifact"]["sha256"]}


def _predict(items: list[PredictionRequest]) -> list[PredictionResponse]:
    """Les quatre étapes d'une prédiction, chronométrées une à une par scripts/bench_predict.py (TP5)."""
    if STATE["model"] is None:
        raise HTTPException(status_code=503, detail="Modèle non chargé")
    # 1. requête(s) validée(s) -> DataFrame
    raw = pd.DataFrame([item.model_dump() for item in items])
    # 2. features : add_features est partagé avec l'entraînement (le recalculer ici créerait un skew)
    features = add_features(raw)[FEATURES]
    # 3. le modèle ONNX
    preds = STATE["model"].run(None, onnx_feeds(features))[0].ravel()
    # 4. bornes [0, capacity] et réponses
    preds = np.clip(preds, 0.0, raw["capacity"].to_numpy())
    version = STATE["metadata"]["model_version"]
    return [
        PredictionResponse(
            station_id=item.station_id,
            target_timestamp=item.timestamp + timedelta(hours=1),
            predicted_bikes=float(p),
            model_version=version,
        )
        for item, p in zip(items, preds, strict=True)
    ]


def _log(responses: list[PredictionResponse]) -> None:
    """Journalise les prédictions (EX-08) ; une base indisponible ne fait jamais échouer la prédiction."""
    url = db.database_url()
    if not url:
        return
    try:
        db.save_predictions(url, responses)
    except Exception:
        logger.exception("Échec de l'enregistrement de la prédiction")


@app.post("/v1/predict", response_model=PredictionResponse)
def predict(request: PredictionRequest) -> PredictionResponse:
    responses = _predict([request])
    _log(responses)
    return responses[0]


@app.post("/v1/predict/batch", response_model=BatchPredictionResponse)
def predict_batch(request: BatchPredictionRequest) -> BatchPredictionResponse:
    responses = _predict(request.items)
    _log(responses)
    return BatchPredictionResponse(predictions=responses)
