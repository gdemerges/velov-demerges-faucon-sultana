#!/usr/bin/env bash
# Pipeline complet depuis un clone propre : PostgreSQL -> ingestion bronze -> dbt build (silver, gold, tests).
# Prérequis : `cp .env.example .env`, un venv avec `pip install -r requirements-pipeline.txt && pip install -e .`
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
export DATABASE_URL="postgresql://velov:${POSTGRES_PASSWORD}@localhost:${DB_PORT:-5432}/velov"

docker compose up -d --wait db
python -m velov.ingest --source "${SOURCE:-simulator}" --days "${DAYS:-30}"
(cd dbt && dbt build --profiles-dir .)
