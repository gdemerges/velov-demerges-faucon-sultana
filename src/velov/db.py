"""Persistance des prédictions dans PostgreSQL (optionnelle : active si DATABASE_URL est définie)."""

from __future__ import annotations

import logging
import os

import psycopg

logger = logging.getLogger("velov.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id               BIGSERIAL PRIMARY KEY,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    station_id       INTEGER NOT NULL,
    target_timestamp TIMESTAMPTZ NOT NULL,
    predicted_bikes  DOUBLE PRECISION NOT NULL,
    model_version    TEXT NOT NULL
)
"""


def database_url() -> str | None:
    return os.getenv("DATABASE_URL") or None


def init_db(url: str) -> None:
    with psycopg.connect(url, connect_timeout=5) as conn:
        conn.execute(SCHEMA)


def save_predictions(url: str, responses) -> None:
    """Une ligne par prédiction (réponses de l'API : station_id, target_timestamp, predicted_bikes, model_version).

    Une connexion par appel, sans pool : c'est ce que le TP5 mesure (README, section Performance).
    """
    rows = [(r.station_id, r.target_timestamp, r.predicted_bikes, r.model_version) for r in responses]
    with psycopg.connect(url, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO predictions (station_id, target_timestamp, predicted_bikes, model_version)"
            " VALUES (%s, %s, %s, %s)",
            rows,
        )
