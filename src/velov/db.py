"""Persistance des prédictions dans PostgreSQL (optionnelle : active si DATABASE_URL est définie).

Chaque ligne garde les ENTRÉES de la prédiction, pas seulement sa sortie (S6, EX-21) :
- la vérité d'une prédiction faite à observed_at est le bikes_available observé à observed_at + 1 h,
  qui arrive avec la requête de l'heure suivante (jointure de velov.monitor, S6 · 19) ;
- la dérive se mesure sur ces entrées (temperature, bikes_available, occupancy) et sur predicted_bikes.
"""

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

# Colonnes ajoutées en S6. ADD COLUMN IF NOT EXISTS : la table de la VM, créée en S2, est migrée sur place.
# Nullables : les lignes antérieures n'ont pas ces entrées ; velov.monitor les ignore (observed_at IS NULL).
MIGRATIONS = [
    "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS observed_at TIMESTAMPTZ",
    "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS capacity INTEGER",
    "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS bikes_available INTEGER",
    "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS temperature DOUBLE PRECISION",
    "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS is_raining BOOLEAN",
    # La jointure de la vérité (station, observed_at + 1 h) et la fenêtre des 7 derniers jours.
    "CREATE INDEX IF NOT EXISTS predictions_station_observed ON predictions (station_id, observed_at)",
]


def database_url() -> str | None:
    return os.getenv("DATABASE_URL") or None


def init_db(url: str) -> None:
    with psycopg.connect(url, connect_timeout=5) as conn:
        conn.execute(SCHEMA)
        for statement in MIGRATIONS:
            conn.execute(statement)


def save_predictions(url: str, items, responses) -> None:
    """Une ligne par prédiction : les entrées de la requête et la réponse de l'API.

    Une connexion par appel, sans pool : c'est ce que le TP5 mesure (README, section Performance).
    """
    rows = [
        (
            r.station_id,
            i.timestamp,
            i.capacity,
            i.bikes_available,
            i.temperature,
            i.is_raining,
            r.target_timestamp,
            r.predicted_bikes,
            r.model_version,
        )
        for i, r in zip(items, responses, strict=True)
    ]
    with psycopg.connect(url, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO predictions (station_id, observed_at, capacity, bikes_available, temperature,"
            " is_raining, target_timestamp, predicted_bikes, model_version)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            rows,
        )
