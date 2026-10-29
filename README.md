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

## Tracer, promouvoir, intégrer (TP4)

```
poste : velov.train --mlflow --register ──┐                     VM · port 5000
        velov.promote [--apply]           ├── MLFLOW_TRACKING_URI ──> mlflow ──> db (base "mlflow") + volume mlartifacts
VM :    scripts/release.sh ── velov.export @champion ──> image velov-api:m<version>-<commit> ──> API
GitHub : workflow ci (lint, tests, secrets, données, image) : ce commit est-il livrable ?
```

**1. Serveur MLflow** (stack fournie par le cours, sur le PostgreSQL du projet, base dédiée `mlflow`) :

```bash
cp .env.example .env                                   # POSTGRES_PASSWORD : jamais dans git ni dans le compose
docker compose -f compose.yaml -f compose.mlflow.yaml up -d --build db mlflow
docker compose -f compose.yaml -f compose.mlflow.yaml ps   # db healthy, mlflow Up, mlflow-db-init Exited (0)
```

Interface : `http://<ip-de-la-vm>:5000`. Les runs et le registre survivent à un `restart` (base PostgreSQL +
volume `mlartifacts`) ; `down -v` les efface. VM injoignable : `export MLFLOW_TRACKING_URI=sqlite:///mlflow.db`.

**2. Runs tracés** (`src/velov/tracking.py`, appelé par `velov.train --mlflow`) : paramètres `seed`,
`learning_rate`, `max_iter`, `max_leaf_nodes`, `test_days` ; métriques de test `mae_model`,
`mae_baseline_persistence`, `mae_model_peak_hours` ; tags `git_commit`, `git_dirty`, `data_sha256` (empreinte du
**contenu**, lignes triées par station et heure), `data_start`, `data_end` ; artefacts : modèle MLflow (skops,
signature) et paquet `package/` (model.onnx + model.joblib + metadata.json).

```bash
export MLFLOW_TRACKING_URI=http://<ip-de-la-vm>:5000     # aucune URL dans le code
python -m velov.data                                     # 90 jours, 20 stations
python -m velov.train --mlflow --register --seed 42      # version du registre, alias @challenger
python -m velov.train --mlflow --seed 42                 # même MAE, même SHA-256 d'artefact
python -m velov.train --mlflow --register --seed 7
```

| Seed | MAE modèle | MAE heures de pointe | MAE persistance | SHA-256 de model.onnx |
|---|---|---|---|---|
| 42 | 1,339 | 1,469 | 1,879 | `b706545fc445` |
| 42 (bis) | 1,339 | 1,469 | 1,879 | `b706545fc445` (identique) |
| 7 | 1,345 | 1,489 | 1,879 | `0bd6e61749a0` |

**Écart entre seeds 42 et 7 : 0,006 vélo (0,45 %)** ; le cours mesure jusqu'à 1,6 % sur les seeds 0 à 3.
Un gain plus petit que ça peut venir du hasard seul : d'où le seuil de 2 % de l'étape 3.

**3. Registre et promotion** (`src/velov/promote.py`) : challenger et champion rechargés depuis le registre et
évalués, avec la persistance, sur la **même fenêtre** (les 14 derniers jours de la table actuelle) ; le challenger
doit battre la persistance, puis le champion d'au moins 2 %. Sans `--apply`, rien ne change.

```bash
python -m velov.promote                 # décision seule
python -m velov.promote --apply         # @champion -> challenger, ancien champion -> @previous-champion, tag promotion
python -m velov.promote --rollback      # @champion <- @previous-champion
python -m velov.data --days 140 --out data/velov_history_140d.csv              # 50 jours de plus
python -m velov.retrain --data data/velov_history_140d.csv --version 0.3.0   # réentraînement + décision
python -m velov.promote --data data/velov_history_140d.csv --apply           # même table, même fenêtre
```

Sorties réelles sur la stack locale : v1 (seed 42) promue, « premier champion » ; v2 (seed 7) refusée,
« gain insuffisant : MAE 1.345 > 1.312 (champion - 2%) » ; v3 réentraînée sur 140 jours promue :

```
Fenêtre de test : 2026-10-04T21:00:00+00:00 -> 2026-10-18T20:00:00+00:00 UTC, 6720 lignes
challenger v3   : MAE 1.371 (heures de pointe 1.458)
champion v1     : MAE 1.506 (heures de pointe 1.672)
persistance     : MAE 1.926
Décision : PROMOUVOIR (gain sur le champion : MAE 1.371 <= 1.476 (champion - 2%))
```

La règle est testée (`tests/test_model_quality.py` : premier champion, gain suffisant, gain dans le bruit,
pire que la persistance, fenêtres différentes) avant d'être appliquée.

**4. CI** (`.github/workflows/ci.yml`, à chaque push et pull request, `permissions: contents: read`) :

| Job | Vérifie | Exigences |
|---|---|---|
| `tests` | `ruff check`, `ruff format --check`, pytest (API, features, contrat, ONNX, modèle, registre) | EX-01, EX-03, EX-05, EX-13, EX-14 |
| `secrets` | gitleaks 8.30.1 sur tout l'historique (`fetch-depth: 0`), binaire vérifié par SHA-256 | EX-06 |
| `data` | ingestion simulée, `dbt build` sur PostgreSQL, entraînement `--from-gold` (Pandera) | EX-10 à EX-12 |
| `image` (après `tests` et `secrets`) | build, `/ready` en 200 sous 60 s (sinon logs), utilisateur non-root, `/v1/model` | EX-02 |

Actions épinglées par SHA de commit (`actions/checkout` v7.0.1, `actions/setup-python` v7.1.0).
Protection de `main` (EX-15), une fois dans les réglages du fork : *Settings > Branches > Add rule* sur `main`,
« Require status checks to pass » avec les quatre jobs du workflow `ci`, et « Require a pull request before merging ».

**Should : livrer le champion sur la VM, et revenir en arrière** ([ADR-0004](docs/adr/0004-livraison-du-modele.md)) :

```bash
./scripts/release.sh                     # export @champion, build velov-api:m3-<commit>, redémarrage de l'API
curl -s localhost:8000/v1/model          # "registry": {"version": "3", "alias": "champion", "run_id": ...}
./scripts/release.sh previous-champion   # retour arrière par le registre
API_IMAGE=velov-api:m1-<commit> docker compose up -d --no-build --wait api   # retour arrière immédiat
```

**Stretch : réentraînement planifié** : crontab de la VM, chaque lundi à 6 h (`velov.retrain` enregistre un
challenger et affiche la décision ; un membre du binôme valide avec `--apply`). La ligne complète est dans
l'en-tête de `src/velov/retrain.py`. Le runner GitHub ne joint pas le registre : pas de réentraînement dans la CI.

| Exigence | Preuve |
|---|---|
| EX-13 modèle retrouvable jusqu'au code et aux données | `GET /v1/model` (bloc `registry`, `data`) ; run avec commit, `git_dirty`, empreinte ; `tests/test_registry.py` |
| EX-14 promotion sur la même fenêtre | sortie de `velov.promote`, tag `promotion` / `promotion_refusee` sur la version, `@previous-champion` |
| EX-15 rien sur main sans CI au vert | workflow `ci` + règle de protection de `main` |

Note : `exercices/s4_audit/` (exercice d'audit d'une CI) n'est pas encore publié sur `cours/main` ;
seuls `compose.mlflow.yaml` et `docker/` ont été récupérés.

IA déclarée : Claude Code (Opus 5.5) a écrit `tracking.py`, `promote.py`, `export.py`, `retrain.py`, les tests de
modèle et de registre, le workflow et `release.sh` à partir de l'énoncé et du cours S4. Vérifié par : pytest
(39 tests), ruff, build et démarrage de l'image, et la séquence complète (runs, promotion, livraison, retour
arrière, redémarrage de la stack) sur une stack MLflow locale.

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
  train.py         entraînement (CSV ou table gold), baseline, artefact + metadata.json
  tracking.py      runs MLflow (paramètres, métriques, commit, empreinte des données), registre, @challenger
  promote.py       règle de promotion, alias champion / previous-champion, retour arrière
  export.py        @alias du registre -> models/ (pour l'image) ; retrain.py : réentraînement planifié
  api/main.py      FastAPI : /health, /ready, /v1/model, /v1/predict
  api/schemas.py   contrat d'entrée / sortie (Pydantic)
dbt/               modèles silver et gold, contrat, tests de qualité
.github/workflows/ ci.yml : lint, tests, secrets, données, image (S4)
compose.mlflow.yaml, docker/   serveur MLflow (S4)
scripts/           run_pipeline.sh, check_idempotence.sh, release.sh
tests/             pytest (features, API, contrat, ONNX, modèle, registre)
docs/              templates : model card, runbook, ADR
exercices/         démo pickle (S1)
```

## Usage de l'IA générative

Chaque activité indique son mode : **sans IA**, **IA déclarée** ou **IA imposée**.
En mode IA déclarée, ajoutez dans la description de vos commits ou de votre rendu :
outil utilisé, ce que vous lui avez demandé, ce que vous avez vérifié ou corrigé.
