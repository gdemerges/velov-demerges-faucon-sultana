# velov-mlops

Projet fil rouge du cours **Industrialisation de l'IA dans le Cloud** (M2 Data Engineering / IA).

Objectif métier : prédire, pour chaque station Vélo'v, le nombre de vélos disponibles **dans une heure**,
et servir cette prédiction via une API fiable, conteneurisée, puis déployée (on-premise et cloud).

## Progression par session

Chaque étape du cours correspond à un tag git. Absent ou bloqué : repartez du tag de fin de la session précédente.

| Tag | Contenu |
|---|---|
| `s1-start` | Données simulées, features, entraînement. API à compléter (TP1) |
| `s1-end` | Modèle v0 servi par FastAPI, tests pytest |

```bash
git checkout s1-end          # récupérer l'état de fin de S1
git checkout -b mon-binome   # travailler sur sa propre branche
```

## Démarrage rapide

Prérequis : Python 3.11+ (3.12 recommandé), git. Docker sera nécessaire en S2.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows : .venv\Scripts\activate
pip install -r requirements-dev.txt     # serving (requirements.txt) + entraînement (requirements-train.txt) + pipeline
pip install -e .                     # rend le package velov importable

python -m velov.data                 # génère data/velov_history.csv (simulateur, seed fixe)
python -m velov.train                # entraîne, exporte models/model.onnx (servi) + model.joblib + metadata.json
python -m velov.train --mlflow       # idem + journalisation MLflow (puis : mlflow ui)
pytest                               # tests
uvicorn velov.api.main:app --reload  # API sur http://127.0.0.1:8000/docs (--reload : en dev uniquement)
```

Exemple d'appel :

```bash
curl -X POST http://127.0.0.1:8000/v1/predict \
  -H "Content-Type: application/json" \
  -d '{"station_id": 3, "timestamp": "2026-10-06T08:00:00+02:00", "capacity": 20,
       "bikes_available": 12, "temperature": 14.5, "is_raining": false}'
```

Le `timestamp` doit porter un fuseau (`+02:00`, `Z`...) : sans fuseau, l'API répond 422.
Les instants sont renvoyés et journalisés en UTC (`"target_timestamp": "2026-10-06T07:00:00Z"`).

## Docker

Depuis un clone propre (le modèle est entraîné pendant le build, rien à générer avant) :

```bash
cp .env.example .env                 # puis changer POSTGRES_PASSWORD
docker compose up -d --build         # API + PostgreSQL ; API_PORT=8001 si le port 8000 est pris
curl localhost:8000/ready            # 200 quand le modèle est chargé
```

Tags d'image, scan Trivy, tailles et publication GHCR : [docs/docker.md](docs/docker.md).

## Pipeline de données (TP3)

```
open data Lyon / simulateur --> bronze.station_status --dbt--> silver_station_status --> gold_training_hourly --> train.py
        velov.ingest            (brut, 1 ligne/station/heure)   (typé, plausible)        (contrat enforced)    (Pandera)
```

Depuis un clone propre :

```bash
cp .env.example .env                         # puis changer POSTGRES_PASSWORD
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-pipeline.txt && pip install -e .
./scripts/run_pipeline.sh                    # PostgreSQL + ingestion simulée (30 jours) + `dbt build`
```

`dbt build` doit finir sur `PASS=16 ... ERROR=0`. Ensuite, entraîner sur la table gold :

```bash
export DATABASE_URL=postgresql://velov:<mot de passe>@localhost:5432/velov     # DB_PORT du .env
python -m velov.train --from-gold --out models/    # le contrat Pandera est vérifié avant d'entraîner
```

Collecte réelle (open data Métropole de Lyon + météo OpenWeatherMap, clé `OPENWEATHER_API_KEY` dans `.env` ; Open-Meteo sans clé à défaut), un relevé par heure :

```bash
docker compose --profile collect up -d --build collector
docker compose logs -f collector                  # "grandlyon : 457 lignes lues, 457 ajoutées"
```

| Couche | Objet | Rôle |
|---|---|---|
| bronze | `bronze.station_status` | copie brute, clé (station, heure UTC), insertion `ON CONFLICT DO NOTHING` |
| silver | `silver_station_status`, `silver_stations` | typage, dédoublonnage, relevés plausibles uniquement (0 <= vélos <= capacité) |
| gold | `gold_training_hourly` | schéma du contrat (7 colonnes, types et contraintes `enforced`), cible `bikes_next_hour` |

Tests de qualité (`dbt/models/*/*.yml`, `dbt/tests/`) : `not_null`, `relationships` (gold -> stations),
capacité dans [1, 80] avec seuil (avertissement dès 1 ligne, échec au-delà de 5), unicité (station, heure),
absence de regard vers le futur. Un test silver en échec empêche la reconstruction de gold.

Choix batch / micro-batch / streaming : [ADR-0002](docs/adr/0002-batch-micro-batch-streaming.md).
Exploitation : [runbook](docs/runbook.md).

| Exigence | Preuve |
|---|---|
| EX-10 rejeu sans effet sur gold | `PATH=.venv/bin:$PATH ./scripts/check_idempotence.sh` |
| EX-11 pas d'information du futur | test dbt `no_lookahead` (dans `dbt build`) |
| EX-12 gold hors contrat jamais publiée | contrat dbt `enforced`, tests bloquants, `pytest tests/test_contract.py` |

## Exigences du projet

Le service doit respecter les exigences de [docs/exigences.md](docs/exigences.md), de S1 à S9.
Chaque rendu indique celles qu'il couvre et comment le vérifier.


## Structure

```
src/velov/
  data.py          simulateur de données (remplacé par l'open data en S3, même schéma)
  features.py      features partagées entraînement / API (anti training-serving skew)
  ingest.py        ingestion bronze idempotente (simulateur ou open data Lyon)
  contract.py      contrat Pandera de la table gold
  train.py         entraînement (CSV ou table gold), baseline, artefact + metadata.json, option MLflow
  api/main.py      FastAPI : /health, /ready, /v1/model, /v1/predict, /v1/predict/batch
  api/schemas.py   contrat d'entrée / sortie (Pydantic)
dbt/               modèles silver et gold, contrat, tests de qualité
scripts/           run_pipeline.sh, check_idempotence.sh
tests/             pytest (features, API, contrat)
docs/              templates : model card, runbook, ADR
exercices/         démo pickle (S1)
```

## Usage de l'IA générative

Chaque activité indique son mode : **sans IA**, **IA déclarée** ou **IA imposée**.
En mode IA déclarée, ajoutez dans la description de vos commits ou de votre rendu :
outil utilisé, ce que vous lui avez demandé, ce que vous avez vérifié ou corrigé.
