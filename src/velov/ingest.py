"""Ingestion bronze : copie brute des relevés de stations dans PostgreSQL, rejouable sans doublon.

Table `bronze.station_status` : une ligne par station et par heure UTC (clé primaire
(station_id, observed_at)). L'insertion est un `ON CONFLICT DO NOTHING` : relancer le même
passage, ou deux passages dans la même heure, n'ajoute aucune ligne (EX-10). Le premier relevé
de l'heure est conservé.

Deux sources, même schéma :
    simulator  historique reproductible (seed fixe), pour démarrer depuis un clone propre
    grandlyon  relevé temps réel Vélo'v (open data Métropole de Lyon) + météo OpenWeatherMap (Open-Meteo à défaut)

Enrichissements collectés à chaque passage grandlyon (données réelles seulement, pour mesurer plus tard
leur apport au modèle) ; un échec de l'un d'eux est journalisé et n'empêche jamais d'enregistrer le relevé :
    bronze.station_info      référentiel des stations : nom, position, commune, catégorie de quartier (pole),
                             altitude (Open-Meteo, une fois par station). Mis à jour sur place (dernière valeur).
    bronze.weather_forecast  prévision horaire Open-Meteo publiée à l'heure du relevé, pour les 3 heures suivantes.
                             Une prévision publiée avant t est une information légitime à t (EX-11).

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

GRANDLYON_URL = (
    "https://data.grandlyon.com/fr/datapusher/ws/rdata/jcd_jcdecaux.jcdvelov/all.json?compact=false&maxfeatures=-1"
)
OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5/weather?lat=45.76&lon=4.84&units=metric&appid={key}"
OPEN_METEO_URL = (
    "https://api.open-meteo.com/v1/forecast?latitude=45.76&longitude=4.84&current=temperature_2m,precipitation"
)
OPEN_METEO_FORECAST_URL = (
    "https://api.open-meteo.com/v1/forecast?latitude=45.76&longitude=4.84"
    "&hourly=temperature_2m,precipitation,precipitation_probability&forecast_hours=4&timezone=UTC"
)
OPEN_METEO_ELEVATION_URL = "https://api.open-meteo.com/v1/elevation?latitude={lat}&longitude={lng}"
ELEVATION_BATCH = 100  # coordonnées par appel (limite de l'API)
FORECAST_HORIZON_HOURS = 3

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
);
CREATE TABLE IF NOT EXISTS bronze.station_info (
    station_id   INTEGER          PRIMARY KEY,
    name         TEXT,
    lat          DOUBLE PRECISION,
    lng          DOUBLE PRECISION,
    commune      TEXT,
    pole         TEXT,                          -- catégorie libre du quartier, telle que publiée
    elevation_m  DOUBLE PRECISION,              -- Open-Meteo, renseignée une fois
    updated_at   TIMESTAMPTZ      NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS bronze.weather_forecast (
    issued_at                 TIMESTAMPTZ      NOT NULL,   -- heure UTC pleine du relevé qui l'a lue
    target_at                 TIMESTAMPTZ      NOT NULL,   -- heure prévue
    temperature               DOUBLE PRECISION,
    precipitation_mm          DOUBLE PRECISION,
    precipitation_probability DOUBLE PRECISION,
    ingested_at               TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (issued_at, target_at)
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


def fetch_grandlyon(now: datetime | None = None, stations: list[dict] | None = None) -> pd.DataFrame:
    """Un relevé de toutes les stations, horodaté à l'heure UTC pleine de la collecte."""
    observed_at = (now or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)
    temperature, is_raining = fetch_weather()
    stations = stations if stations is not None else _get_json(GRANDLYON_URL)["values"]
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


def station_info_rows(stations: list[dict]) -> list[tuple]:
    """Référentiel tiré du même flux que le relevé : (station_id, nom, lat, lng, commune, pole)."""
    return [
        (int(s["number"]), s.get("name"), s.get("lat"), s.get("lng"), s.get("commune"), (s.get("pole") or "").strip())
        for s in stations
        if s.get("number") is not None
    ]


def forecast_rows(payload: dict, issued_at: datetime) -> list[tuple]:
    """Prévisions des FORECAST_HORIZON_HOURS heures suivant issued_at (issued_at exclu : c'est le présent)."""
    hourly = payload["hourly"]
    rows = []
    for i, t in enumerate(hourly["time"]):
        target = datetime.fromisoformat(t).replace(tzinfo=UTC)
        hours = (target - issued_at).total_seconds() / 3600
        if 1 <= hours <= FORECAST_HORIZON_HOURS:
            rows.append(
                (
                    issued_at,
                    target,
                    hourly["temperature_2m"][i],
                    hourly["precipitation"][i],
                    hourly["precipitation_probability"][i],
                )
            )
    return rows


def load_station_info(url: str, rows: list[tuple]) -> int:
    """Insère ou met à jour le référentiel ; l'altitude déjà connue est conservée."""
    with psycopg.connect(url, connect_timeout=5) as conn:
        conn.execute(DDL)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO bronze.station_info (station_id, name, lat, lng, commune, pole)"
                " VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (station_id) DO UPDATE SET name = EXCLUDED.name, lat = EXCLUDED.lat,"
                " lng = EXCLUDED.lng, commune = EXCLUDED.commune, pole = EXCLUDED.pole, updated_at = now(),"
                # Une station déplacée doit retrouver son altitude : on l'efface si la position change.
                " elevation_m = CASE WHEN bronze.station_info.lat IS DISTINCT FROM EXCLUDED.lat"
                "   OR bronze.station_info.lng IS DISTINCT FROM EXCLUDED.lng"
                "   THEN NULL ELSE bronze.station_info.elevation_m END",
                rows,
            )
    return len(rows)


def fill_elevations(url: str) -> int:
    """Altitude des stations qui n'en ont pas encore (Open-Meteo, par paquets de ELEVATION_BATCH)."""
    with psycopg.connect(url, connect_timeout=5) as conn:
        missing = conn.execute(
            "SELECT station_id, lat, lng FROM bronze.station_info"
            " WHERE elevation_m IS NULL AND lat IS NOT NULL AND lng IS NOT NULL ORDER BY station_id"
        ).fetchall()
        for i in range(0, len(missing), ELEVATION_BATCH):
            batch = missing[i : i + ELEVATION_BATCH]
            lat = ",".join(f"{r[1]:.6f}" for r in batch)
            lng = ",".join(f"{r[2]:.6f}" for r in batch)
            elevations = _get_json(OPEN_METEO_ELEVATION_URL.format(lat=lat, lng=lng))["elevation"]
            with conn.cursor() as cur:
                cur.executemany(
                    "UPDATE bronze.station_info SET elevation_m = %s WHERE station_id = %s",
                    [(e, r[0]) for e, r in zip(elevations, batch, strict=True)],
                )
    return len(missing)


def load_forecast(url: str, rows: list[tuple]) -> int:
    with psycopg.connect(url, connect_timeout=5) as conn:
        conn.execute(DDL)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO bronze.weather_forecast"
                " (issued_at, target_at, temperature, precipitation_mm, precipitation_probability)"
                " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (issued_at, target_at) DO NOTHING",
                rows,
            )
            return max(cur.rowcount, 0)


def enrich(url: str, stations: list[dict], issued_at: datetime) -> None:
    """Référentiel, altitude et prévision : chaque étape échoue seule, sans toucher au relevé déjà enregistré."""
    steps = [
        ("référentiel des stations", lambda: load_station_info(url, station_info_rows(stations))),
        ("altitudes", lambda: fill_elevations(url)),
        ("prévision météo", lambda: load_forecast(url, forecast_rows(_get_json(OPEN_METEO_FORECAST_URL), issued_at))),
    ]
    for name, step in steps:
        try:
            logger.info("%s : %d ligne(s)", name, step())
        except Exception as exc:
            logger.warning("Enrichissement « %s » échoué (%s) : relevé conservé", name, exc)


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
            stations = None
            if args.source == "simulator":
                df = fetch_simulator(args.days, args.stations, args.start, args.seed)
            else:
                stations = _get_json(GRANDLYON_URL)["values"]
                df = fetch_grandlyon(stations=stations)
            added = load_bronze(url, df, args.source)
            logger.info(
                "%s : %d lignes lues, %d ajoutées, %d déjà présentes", args.source, len(df), added, len(df) - added
            )
            if stations is not None:
                enrich(url, stations, df["observed_at"].iloc[0])
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
