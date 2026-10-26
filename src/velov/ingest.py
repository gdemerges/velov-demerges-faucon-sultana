"""Ingestion bronze : copie brute des relevés de stations dans PostgreSQL, rejouable sans doublon.

Table `bronze.station_status` : une ligne par station et par heure UTC (clé primaire
(station_id, observed_at)). L'insertion est un `ON CONFLICT DO NOTHING` : relancer le même
passage, ou deux passages dans la même heure, n'ajoute aucune ligne (EX-10). Le premier relevé
de l'heure est conservé.

Deux sources, même schéma :
    simulator  historique reproductible (seed fixe), pour démarrer depuis un clone propre
    grandlyon  relevé temps réel Vélo'v (open data Métropole de Lyon) + météo OpenWeatherMap (Open-Meteo à défaut)

Usage :
    python -m velov.ingest --source simulator --days 30
    python -m velov.ingest --source grandlyon                  # un relevé
    python -m velov.ingest --source grandlyon --every 3600     # collecte en continu (service compose)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import urllib.request
from datetime import UTC, datetime

import pandas as pd
import psycopg

from velov.data import simulate

logger = logging.getLogger("velov.ingest")

GRANDLYON_URL = "https://data.grandlyon.com/fr/datapusher/ws/rdata/jcd_jcdecaux.jcdvelov/all.json?compact=false&maxfeatures=-1"
OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5/weather?lat=45.76&lon=4.84&units=metric&appid={key}"
OPEN_METEO_URL = (
    "https://api.open-meteo.com/v1/forecast?latitude=45.76&longitude=4.84&current=temperature_2m,precipitation"
)

RAIN_CONDITIONS = {"Rain", "Drizzle", "Thunderstorm"}
COLUMNS = ["station_id", "observed_at", "capacity", "bikes_available", "temperature", "is_raining"]

DDL = """
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE TABLE IF NOT EXISTS bronze.station_status (
    station_id       INTEGER          NOT NULL,
    observed_at      TIMESTAMPTZ      NOT NULL,   -- heure UTC pleine du relevé
    capacity         INTEGER,
    bikes_available  INTEGER,
    temperature      DOUBLE PRECISION,
    is_raining       BOOLEAN,
    source           TEXT             NOT NULL,
    ingested_at      TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (station_id, observed_at)
)
"""


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (URL https constante)
        return json.load(resp)


def fetch_weather() -> tuple[float, bool]:
    """(température °C, pluie ?) à Lyon. OpenWeatherMap si OPENWEATHER_API_KEY est définie, sinon Open-Meteo.

    Si OpenWeatherMap échoue (clé pas encore activée, quota, panne), on se rabat sur Open-Meteo :
    mieux vaut une météo d'une autre source qu'un relevé de stations perdu pour toujours.
    """
    key = os.getenv("OPENWEATHER_API_KEY")
    if key:
        try:
            data = _get_json(OPENWEATHER_URL.format(key=key))
            raining = bool(data.get("rain", {}).get("1h", 0)) or data["weather"][0]["main"] in RAIN_CONDITIONS
            return float(data["main"]["temp"]), raining
        except Exception as exc:
            # exc seul : l'URL (donc la clé) n'est pas journalisée.
            logger.warning("OpenWeatherMap indisponible (%s), repli sur Open-Meteo", type(exc).__name__)
    current = _get_json(OPEN_METEO_URL)["current"]
    return float(current["temperature_2m"]), current["precipitation"] > 0


def fetch_simulator(days: int, stations: int, start: str, seed: int) -> pd.DataFrame:
    df = simulate(days=days, n_stations=stations, start=start, seed=seed)
    return df.rename(columns={"timestamp": "observed_at"})[COLUMNS]


def fetch_grandlyon(now: datetime | None = None) -> pd.DataFrame:
    """Un relevé de toutes les stations, horodaté à l'heure UTC pleine de la collecte."""
    observed_at = (now or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)
    temperature, is_raining = fetch_weather()
    stations = _get_json(GRANDLYON_URL)["values"]
    rows = [
        {
            "station_id": int(s["number"]),
            "observed_at": observed_at,
            "capacity": s.get("bike_stands"),
            "bikes_available": s.get("available_bikes"),
            "temperature": temperature,
            "is_raining": is_raining,
        }
        for s in stations
    ]
    return pd.DataFrame(rows, columns=COLUMNS)


def load_bronze(url: str, df: pd.DataFrame, source: str) -> int:
    """Insère les lignes absentes, retourne le nombre de lignes réellement ajoutées."""
    # `object` et non `bool`/`float` : NaN/None deviennent NULL côté PostgreSQL.
    records = df[COLUMNS].astype(object).where(df[COLUMNS].notna(), None).itertuples(index=False, name=None)
    with psycopg.connect(url, connect_timeout=5) as conn:
        conn.execute(DDL)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO bronze.station_status"
                " (station_id, observed_at, capacity, bikes_available, temperature, is_raining, source)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (station_id, observed_at) DO NOTHING",
                [(*r, source) for r in records],
            )
            return max(cur.rowcount, 0)


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Ingestion bronze idempotente des relevés Vélo'v.")
    parser.add_argument("--source", choices=["simulator", "grandlyon"], default="simulator")
    parser.add_argument("--days", type=int, default=30, help="simulator : durée de l'historique")
    parser.add_argument("--stations", type=int, default=20, help="simulator : nombre de stations")
    parser.add_argument("--start", default="2026-06-01", help="simulator : premier jour")
    parser.add_argument("--seed", type=int, default=42, help="simulator : graine")
    parser.add_argument("--every", type=int, default=0, help="grandlyon : secondes entre deux relevés (0 = un seul)")
    args = parser.parse_args()

    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL non définie (ex. postgresql://velov:<mdp>@localhost:5432/velov)")

    while True:
        try:
            if args.source == "simulator":
                df = fetch_simulator(args.days, args.stations, args.start, args.seed)
            else:
                df = fetch_grandlyon()
            added = load_bronze(url, df, args.source)
            logger.info(
                "%s : %d lignes lues, %d ajoutées, %d déjà présentes", args.source, len(df), added, len(df) - added
            )
        except Exception:
            # En collecte continue, un échec ponctuel (réseau, API) ne doit pas tuer le service :
            # le prochain tour réessaie, et le trou reste visible dans la table.
            if not args.every:
                raise
            logger.exception("Relevé %s échoué, nouvel essai dans %d s", args.source, args.every)
        if not args.every:
            return
        time.sleep(args.every)


if __name__ == "__main__":
    main()
