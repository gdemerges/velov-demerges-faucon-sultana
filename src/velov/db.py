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


def save_prediction(url: str, station_id: int, target_timestamp, predicted_bikes: float, model_version: str) -> None:
    with psycopg.connect(url, connect_timeout=5) as conn:
        conn.execute(
            "INSERT INTO predictions (station_id, target_timestamp, predicted_bikes, model_version)"
            " VALUES (%s, %s, %s, %s)",
            (station_id, target_timestamp, predicted_bikes, model_version),
        )
