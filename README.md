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

## Performance (TP5) : mesurer, trouver le goulot, optimiser

SLO (EX-16) : **95 % des `POST /v1/predict` sous 200 ms, moins de 1 % d'erreurs.** Exploitation : [runbook, section 8](docs/runbook.md).

### Protocole

**Mesure locale** (VM indisponible) : l'API et Locust sur le même poste ; l'API bornée à **un cœur**
(`docker update --cpus 1`), Locust sur les 19 autres.

| Élément | Valeur |
|---|---|
| Poste | Windows 11 Pro, 20 cœurs logiques, 7,6 Go pour Docker Desktop 29.8.2 (WSL2), sur secteur |
| API | image `velov-api:tp5-<commit>` (Python 3.12, uvicorn 0.46, onnxruntime 1.31), modèle 0.1.0 entraîné dans le build, PostgreSQL 16 du compose actif (journalisation mesurée avec) |
| Charge | Locust 2.47.0 (`loadtest/locustfile.py` du cours : 10 prédictions pour 1 `/health`, payloads tirés au hasard, 0,5 à 1,5 s de réflexion) |
| Paliers | `loadtest/paliers.sh` : échauffement 1 min non compté, puis 10, 25, 50, 100, 150 usagers, 2 min chacun |
| Commits | `w1` : 7d761c9 · `w1fix` et `w2` : 88cc302 (horodatages dans `results/<config>/protocole.txt`) |
| Arrêté pendant les mesures | MLflow, IDE et navigateur lourds ; restaient les conteneurs d'un autre projet (Airflow, 1 à 9 % d'un cœur au total, `docker_stats.txt`) |
| Lecture | `python loadtest/summary.py results/<config> --markdown` (ligne `/v1/predict` seule) ; courbe : `python scripts/plot_paliers.py results/w1 results/w1fix results/w2` |

Tous les fichiers bruts sont dans [`results/`](results/) (CSV Locust, `docker stats` aux paliers 100 et 150, compteurs
cgroup `cpu.stat`) ; ils serviront au TP6 pour comparer ces p95 à ceux de Prometheus.

### Courbe de saturation

![Débit et p95 selon les usagers](docs/perf/saturation.png)

**`w1` : 1 worker, 1 cœur, tel que livré au TP4** (commit 7d761c9)

| usagers | requêtes | erreurs % | débit req/s | p50 ms | p95 ms | p99 ms | SLO p95 |
|---|---|---|---|---|---|---|---|
| 10 | 651 | 0.0 | 5.5 | 700 | 1400 | 1700 | KO |
| 25 | 774 | 0.0 | 6.5 | 2600 | 3700 | 4600 | KO |
| 50 | 838 | 0.0 | 7.1 | 5500 | 7200 | 8000 | KO |
| 100 | 791 | 0.0 | 6.7 | 12000 | 17000 | 19000 | KO |
| 150 | 771 | 0.0 | 6.5 | 20000 | 24000 | 26000 | KO |

**Aucun palier ne tient le SLO** : 5,5 req/s et p95 de 1,4 s dès 10 usagers, débit plafonné à ~7 req/s, l'API à
99 % de son cœur. La latence monte avec les usagers et le débit ne bouge plus : l'API est saturée dès le départ.

### Le goulot, et la mesure qui le prouve

**Goulot : onnxruntime lançait 20 threads de calcul dans un conteneur limité à un cœur.** Dans le conteneur,
`os.cpu_count()` vaut 20 (les cœurs de l'hôte) ; ORT crée un thread par cœur, qui attend en boucle active. Le quota
d'un cœur est consommé en attente et le noyau bride tout le process, y compris le code pandas.

| Preuve | Avant (ORT par défaut) | Après (ORT 1 thread) |
|---|---|---|
| Compteurs cgroup `nr_throttled / nr_periods` pendant les paliers | **7 475 / 7 676 (97 %)** | 1 967 / 8 045 (24 %, et seulement aux paliers saturés) |
| `_predict()` dans le conteneur borné, p50 / p95 (`bench_predict.py --ort-threads 0 1`) | 7,7 ms / **105,5 ms** | 3,3 ms / **3,9 ms** |
| `add_features` seul (pandas, sans ORT), p95 | 94 ms | 1,6 ms |

**Temps par étape** de `_predict()`, ORT sur 1 thread, dans le conteneur borné à un cœur, 500 appels après
50 d'échauffement, `time.perf_counter` (`results/bench_predict_batch.md`) :

| Lot | 1. DataFrame | 2. add_features | 3. modèle | 4. réponses | total p50 | total p95 | par ligne |
|---|---|---|---|---|---|---|---|
| 1 ligne | 0,27 ms | 1,39 ms | 1,71 ms | 0,06 ms | 3,44 ms | 3,86 ms | 3 440 µs |
| 100 lignes | 0,46 ms | 1,50 ms | 2,01 ms | 0,22 ms | 4,20 ms | 4,86 ms | 42 µs |
| 1 000 lignes | 2,00 ms | 2,51 ms | 5,05 ms | 1,78 ms | 11,34 ms | 14,82 ms | 11 µs |

Prédire 1 ligne coûte presque autant que 100 : le coût est **fixe** (construire un DataFrame, appeler pandas, passer
la frontière Python / C++ d'ORT), pas proportionnel aux lignes. D'où l'intérêt de `/v1/predict/batch` et d'un
batching dynamique (extensions).

**Où part le reste du temps.** À 10 usagers (pas de file d'attente) : `_predict()` 3,4 ms ; `X-Process-Time-Ms`
(tout le temps passé dans l'API) **15 à 20 ms** ; curl depuis le poste ~20 ms ; Locust p50 **59 ms**.
- Entre `_predict` et `X-Process-Time-Ms` : la **journalisation**, une connexion PostgreSQL ouverte par requête.
  `bench_predict.py --database-url` : 21,7 ms au p50, **75 % du temps** d'une prédiction (`results/bench_journalisation.md`,
  mesuré depuis le poste à travers le port Docker, un peu plus cher que dans le réseau du compose).
- Entre curl et Locust (~40 ms) : côté client. Locust (gevent) sur Windows, sur le même poste ; à refaire depuis
  une autre machine quand la VM revient. La latence Locust est donc pessimiste.
- Au palier saturé, l'écart entre Locust et `X-Process-Time-Ms` grandit : c'est l'attente dans la file.

**Une seule modification : ORT sur 1 thread** (`velov.artifact.onnx_session`, `ORT_INTRA_OP_THREADS`, défaut 1 ;
une requête porte une ligne, il n'y a rien à paralléliser dedans). Mêmes paliers, même cœur :

**`w1fix` : 1 worker, 1 cœur, ORT 1 thread** (commit 88cc302)

| usagers | requêtes | erreurs % | débit req/s | p50 ms | p95 ms | p99 ms | SLO p95 |
|---|---|---|---|---|---|---|---|
| 10 | 1015 | 0.0 | 8.6 | 59 | 66 | 85 | ok |
| 25 | 2516 | 0.0 | 21.2 | 60 | 71 | 88 | ok |
| 50 | 5016 | 0.0 | 42.3 | 62 | 84 | 120 | ok |
| 100 | 8858 | 0.0 | 74.2 | 170 | 610 | 920 | KO |
| 150 | 8880 | 0.0 | 74.2 | 900 | 1400 | 1500 | KO |

**Capacité sous le SLO : ~42 req/s (50 usagers) ; plafond 74 req/s**, coude entre 50 et 100 usagers.

Palier du coude refait (`results/w1fix_bis`, 100 usagers) : 71,5 req/s et p95 720 ms contre 74,2 req/s et 610 ms, soit 4 % d'écart sur le débit : sous les 20 %, la mesure est reproductible.

### Étape 4 : un worker et un cœur de plus, le débit double-t-il ?

Seul changement par rapport à `w1fix` : `compose.workers.yaml` (`--workers 2`) et `docker update --cpus 2`.

| usagers | requêtes | erreurs % | débit req/s | p50 ms | p95 ms | p99 ms | SLO p95 |
|---|---|---|---|---|---|---|---|
| 10 | 950 | 0.0 | 8.7 | 59 | 65 | 70 | ok |
| 25 | 2349 | 0.0 | 21.0 | 59 | 68 | 100 | ok |

**Mesure interrompue après le palier 25 (manque de temps) : non concluante.** Aux paliers 10 et 25, le débit est le même qu'avec un worker (8,7 et 21 req/s) : il est fixé par les usagers (loi de Little, ~1 req/s par usager), pas par l'API. La question « le débit double-t-il ? » se tranche aux paliers 100 et 150, à relancer : `./loadtest/paliers.sh http://localhost:8000 w2`. Attendu : plafond vers 2 × 74 req/s si le CPU de l'API reste le goulot, moins si la base (35 % d'un cœur à 74 req/s) ou Locust saturent ; chaque worker charge son modèle (~100 Mo par process).

### Comparaison des configurations

| Config | Capacité sous le SLO | Plafond | p95 à 50 usagers |
|---|---|---|---|
| w1 (ORT 20 threads) | 0 req/s | ~7 req/s | 7 200 ms |
| w1fix (ORT 1 thread) | ~42 req/s | 74 req/s | 84 ms |
| w2 (2 workers) | à mesurer (paliers 100-150 non faits) | — | — |

Conclusion chiffrée : une modification d'une ligne (threads ORT) multiplie le plafond par 10 (7 -> 74 req/s) et ramène le p95 à 50 usagers de 7,2 s à 84 ms.

## Optimiser le modèle (TP5, Should et Stretch)

### TP5a : DistilBERT en float32 contre int8

**Non fait** (manque de temps) : le script est prêt, `scripts/bench_quantization.py` (float32, int8 torch.ao, int8 torchao ; taille, p50/p95 sur 1 thread, précision SST-2 et accord avec float32). À lancer à la maison selon l'énoncé.

### Moteur d'inférence : ONNX contre scikit-learn (Stretch, EX-17)

Le modèle Vélo'v est servi en ONNX depuis le TP2 ([ADR-0003](docs/adr/0003-serving-onnx.md)). L'export est maintenant
vérifié sur **tout l'historique** (et plus seulement sur le test) : refusé au-delà de 0,001 vélo d'écart. Triplet
mesuré dans l'ADR-0003 (section « Triplet mesuré »).

### Audit d'un test de charge écrit par une IA

Analyse des six défauts (ligne, effet sur le verdict, correction) et demande réécrite : [exercices/s5_audit/AUDIT.md](exercices/s5_audit/AUDIT.md). Le script n'a pas encore été lancé contre l'API (étapes 1-2 de l'audit) ; attendu : 100 % de 422 comptés comme succès (timestamp sans fuseau), d'où un « SLO respecté » sans aucune prédiction.

### Ce qu'on ferait ensuite (extensions, protocole de l'étape 2)

- **Journalisation** : 75 % du temps d'une prédiction. Pool de connexions (psycopg_pool) ou `BackgroundTasks`, puis
  paliers avec et sans `DATABASE_URL`.
- **Batching dynamique** : 1 ligne coûte presque autant que 100 ; regrouper les requêtes unitaires (asyncio.Queue).
- **Charge en boucle ouverte** (k6 `constant-arrival-rate` au débit du coude) : Locust est en boucle fermée et
  sous-estime la latence d'un vrai trafic.

IA déclarée (TP5) : Claude Code (Opus 5.5) a écrit `scripts/bench_predict.py`, `scripts/bench_quantization.py`,
`scripts/plot_paliers.py`, `compose.workers.yaml`, `onnx_session`, le middleware `X-Process-Time-Ms`, la route batch
et leurs tests, a lancé les paliers et rédigé cette section, le runbook et l'audit. Vérifié par : pytest (42 tests),
ruff, la somme des étapes du banc comparée à `_predict()` appelé tel quel, les compteurs cgroup avant et après, la
répétition du palier du coude. Corrigé en cours de route : les paliers relancés sans `MSYS_NO_PATHCONV` (Git Bash
réécrivait les chemins passés à Locust), et la première capture des compteurs cgroup (chemin réécrit de la même façon).
L'audit du locustfile IA (six défauts) a été rédigé par l'assistant, pas « sans assistant » comme le demande l'énoncé :
à refaire en binôme avant l'oral.


## Exploiter le service (TP6) : HTTPS, métriques, alertes, dérive

Sur la VM (`192.168.1.234`, dépôt `~/velov-mlops`, branche `tp6`), la stack se lance avec trois fichiers compose,
déclarés une fois pour toutes dans `.env` (`COMPOSE_FILE=compose.yaml:compose.mlflow.yaml:compose.monitoring.yaml`).

```
poste ──HTTPS :443──> proxy (Nginx, certificat auto-signé) ──> api :8000 ──> db (predictions)
                         └── /grafana/ ──> grafana ──> prometheus :9090 ──lit /metrics toutes les 15 s──> api
VM :  velov.monitor ──> db (127.0.0.1:5432) : dérive (Evidently) et MAE réelle face à la persistance
```

```bash
./scripts/make_cert.sh 192.168.1.234     # certs/ (ignoré par git) ; velov.crt se donne aux clients
docker compose up -d --build --wait
```

| Étape | Ce qui a été fait | Preuve (VM, 20/11) |
|---|---|---|
| 1. HTTPS | `docker/nginx/velov.conf` : HTTP redirigé en 301, TLS 1.2+, `/metrics` en 404, port 8000 plus publié | les cinq appels du runbook (section 3) : 301, 60, 200, 404, 502 ; `curl :8000` sans réponse |
| 2. Métriques et alertes | `prometheus-fastapi-instrumentator` (seau à 0,2 s), `velov_model_info` ; `alerts.yml` : SLI, `LatenceHorsSLO`, `Erreurs5xx`, `ApiInjoignable`, burn rate | `promtool check rules` : 7 règles ; cible `velov-api` UP ; SLI calculé sous trafic ; API arrêtée à 09:17:54, `ApiInjoignable` firing à 09:18:55 |
| 3. Tableau de bord | `docker/grafana/dashboards/velov.json` : débit par route, part des 5xx, SLI (seuil 0,95), modèle servi, et l'erreur réelle par heure (Should) | provisionné dans le dossier Velov ; servi sous `https://<VM>/grafana/`, port 3000 fermé |
| 4. Modèle | `velov.monitor` (`src/velov/monitor.py`) ; la table `predictions` garde désormais les entrées (`observed_at`, `bikes_available`...) | trois semaines rejouées, rapport commenté dans la [model card](docs/model_card.md) (section 8) |

Le monitor, sur la VM :
```bash
set -a && . ./.env && set +a
export DATABASE_URL=postgresql+psycopg://velov:$POSTGRES_PASSWORD@127.0.0.1:5432/velov
docker compose cp api:/app/models/. models/served/      # le modèle réellement servi
python -m velov.replay --url https://192.168.1.234 --cacert certs/velov.crt --scenario normal
python -m velov.monitor --db "$DATABASE_URL" --data data/velov_history.csv --model-dir models/served --out reports/
```

| Semaine rejouée | Dérive principale | MAE modèle | MAE persistance | Ratio (entraînement 0,71) | Code de sortie |
|---|---|---|---|---|---|
| Normale | `temperature` 0,39 | 1,309 | 1,821 | 0,72 | 0 |
| Froid | `temperature` 4,44 | 1,419 | 1,821 | 0,78 | 0 |
| Déménagement | occupation et prédictions 0,25 à 0,30 | 1,584 | 1,818 | 0,87 | 1 : 7 stations pires que la persistance |

Le ratio global ne voit pas le déménagement (0,87 < 0,90). L'alerte vient du suivi par station : les 7 stations
signalées sont exactement les stations de bureaux déplacées. Les alertes et la conduite à tenir sont dans le
[runbook](docs/runbook.md), section 10. Exigences couvertes : EX-19, EX-20 et EX-21
([docs/exigences.md](docs/exigences.md)).

**Should.** Faits :
- Grafana derrière le proxy ;
- alerte de burn rate sur deux fenêtres (1 h et 5 min, facteur 6, justifiés dans le runbook) ;
- erreur réelle par heure dans Grafana (utilisateur `grafana_ro`, lecture seule).

Non fait : l'audit de la supervision écrite par une IA, noté individuellement, à faire en binôme sans assistant.

**Extension choisie** : Monitoring ML planifié (★★★), [docs/extension.md](docs/extension.md).

IA déclarée (TP6) : Claude Code (Opus 5.5) a écrit l'instrumentation, la migration de la table `predictions`,
les règles d'alerte, le tableau de bord, `velov.monitor` et ses tests, la mise derrière le proxy de Grafana et
sa source PostgreSQL. Il a aussi installé la stack sur la VM, rejoué les semaines et rédigé ce texte, le runbook
(sections 3 et 10), la section Monitoring de la model card et le brouillon de la fiche d'extension.

Vérifications :
- pytest (52 tests) et ruff ;
- les cinq appels HTTPS depuis le poste, ports 8000 et 3000 fermés ;
- `promtool`, et `ApiInjoignable` provoquée ;
- les chiffres du monitor comparés à ceux du cours ;
- les écritures refusées pour `grafana_ro`.

Corrigés en cours de route :
- `velov.replay` (fourni) importait `load_table`, absent de notre `train.py` ;
- `release.sh` interrogeait le port 8000, qui n'est plus publié ;
- le test de dérive : sous 1 000 lignes, Evidently renvoie une p-value et non une distance.

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
  replay.py        rejoue une semaine contre l'API (S6, fourni) ; monitor.py : dérive et erreur réelle (S6)
  api/main.py      FastAPI : /health, /ready, /v1/model, /v1/predict, /metrics (S6)
  api/schemas.py   contrat d'entrée / sortie (Pydantic)
dbt/               modèles silver et gold, contrat, tests de qualité
.github/workflows/ ci.yml : lint, tests, secrets, données, image (S4)
compose.mlflow.yaml, docker/   serveur MLflow (S4)
compose.monitoring.yaml        proxy Nginx (TLS), Prometheus, Grafana (S6) : docker/nginx, docker/prometheus, docker/grafana
scripts/           run_pipeline.sh, check_idempotence.sh, release.sh
tests/             pytest (features, API, contrat, ONNX, modèle, registre)
docs/              templates : model card, runbook, ADR
exercices/         démo pickle (S1)
```

## Usage de l'IA générative

Chaque activité indique son mode : **sans IA**, **IA déclarée** ou **IA imposée**.
En mode IA déclarée, ajoutez dans la description de vos commits ou de votre rendu :
outil utilisé, ce que vous lui avez demandé, ce que vous avez vérifié ou corrigé.
