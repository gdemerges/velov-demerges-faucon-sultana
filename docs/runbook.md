# Runbook : API velov-availability

> Public cible : une personne d'astreinte qui ne connaît pas le projet et doit rétablir le service à 3 h du matin.
> Toutes les commandes se lancent depuis le dossier du dépôt (sur la VM : `~/velov-mlops`).

## 1. Vue d'ensemble
- Ce que fait le service : pour une station Vélo'v et un instant donné, l'API prédit le nombre de vélos
  disponibles une heure plus tard (`POST /v1/predict`, ou `/v1/predict/batch` jusqu'à 1000 lignes).
  Chaque prédiction porte la version du modèle et est journalisée en base.
- Schéma d'architecture : README, sections Docker, Pipeline (TP3) et TP4.
- Dépendances : PostgreSQL (`db`, journal `predictions`, tables bronze/silver/gold, registre MLflow) ;
  le modèle est **dans l'image** (`/app/models/model.onnx` + `metadata.json`, empreinte vérifiée au démarrage).
  Sans base, l'API prédit quand même (journalisation désactivée, erreur dans les logs).
- SLO : 95 % des `/v1/predict` sous 200 ms, moins de 1 % d'erreurs (section 8).

## 2. Démarrer / arrêter
```bash
cp .env.example .env                      # une seule fois ; POSTGRES_PASSWORD à changer, jamais dans git
docker compose up -d --build --wait       # démarrer API + base (attend les healthchecks)
docker compose stop                       # arrêter sans rien perdre (le volume pgdata reste)
docker compose restart api                # redémarrer un seul service
docker compose logs --tail 100 -f api     # suivre les logs
```
Ne jamais lancer `docker compose down -v` sur la VM : `-v` supprime le volume de la base (journal, registre).

## 3. Vérifier que tout va bien
| Vérification | Commande | Résultat attendu |
|---|---|---|
| Le process répond | `curl -s http://<hôte>:8000/health` | `{"status":"ok"}` |
| Le modèle est chargé | `curl -s http://<hôte>:8000/ready` | HTTP 200 + version |
| Quel modèle tourne | `curl -s http://<hôte>:8000/v1/model` | version, `registry.version`, métriques, données |
| Conteneurs sains | `docker compose ps` | `api` et `db` `Up (healthy)` |
| Une prédiction est journalisée | `docker compose exec db psql -U velov -d velov -c "select * from predictions order by id desc limit 3"` | lignes récentes, `created_at` en UTC |
| Temps passé dans l'API | `curl -s -D - -o /dev/null http://<hôte>:8000/ready \| grep -i x-process-time` | quelques ms |

## 4. Incidents connus
| Symptôme | Cause probable | Diagnostic | Action |
|---|---|---|---|
| `/ready` renvoie 503 | modèle absent ou empreinte SHA-256 invalide (artefact modifié) | `docker compose logs api \| grep -i "chargement du modèle"` | reconstruire l'image (`docker compose up -d --build api`) ou revenir à l'image précédente (section 5) |
| Le conteneur redémarre en boucle | `.env` absent (`POSTGRES_PASSWORD` requis), port 8000 déjà pris | `docker compose ps -a`, `docker compose logs api` | créer `.env` ; `API_PORT=8001 docker compose up -d` |
| Prédictions non journalisées | base indisponible ou `DATABASE_URL` erronée | `docker compose logs api \| grep -i "base de données"` ; `docker compose ps db` | `docker compose up -d db`, puis `docker compose restart api` |
| Latence élevée | file d'attente (débit proche de la capacité) ou CPU bridé | section 8 : comparer `X-Process-Time-Ms` à la latence client ; `docker stats --no-stream` | section 8, « près du plafond » |
| `422` en masse côté client | client hors contrat (timestamp sans fuseau, champ inconnu, vélos > capacité) | corps de la réponse (`detail`) | corriger le client : ce n'est pas une panne de l'API (EX-01) |

## 5. Déployer une nouvelle version du modèle
Sur la VM (le serveur MLflow y est joignable, `MLFLOW_TRACKING_URI=http://127.0.0.1:5000`) :
1. Vérifier la décision : `python -m velov.promote --from-gold` (challenger, champion et persistance sur la
   même fenêtre). Si elle dit PROMOUVOIR et qu'on est d'accord : `python -m velov.promote --from-gold --apply`
   (l'ancien champion prend l'alias `previous-champion`, la raison est taguée sur la version).
2. Livrer : `git pull origin main && ./scripts/release.sh` (export de `@champion`, image
   `velov-api:m<version>-<commit>`, redémarrage de l'API, attend le healthcheck).
3. Contrôler : `curl -s localhost:8000/v1/model` -> `registry.version` est la nouvelle version.
4. Retour arrière (rollback) :
   - le plus rapide, sans build : `docker image ls velov-api`, puis
     `API_IMAGE=velov-api:m<ancienne version>-<commit> docker compose up -d --no-build --wait api` ;
   - par le registre : `python -m velov.promote --rollback` (champion <- previous-champion), puis
     `./scripts/release.sh`. `./scripts/release.sh previous-champion` livre l'ancien modèle sans toucher aux alias.

### Serveur MLflow
| Vérification | Commande | Attendu |
|---|---|---|
| La stack tourne | `docker compose -f compose.yaml -f compose.mlflow.yaml ps` | `db` healthy, `mlflow` Up (healthy), `mlflow-db-init` Exited (0) |
| Le serveur répond | `curl -s localhost:5000/health` | `OK` |
| 403 `Invalid Host header` | en-tête Host absent de `--allowed-hosts` (MLflow >= 3.5) | ajouter l'hôte:port dans `compose.mlflow.yaml` |

`docker compose down -v` supprime aussi le registre (base `mlflow` dans le volume `pgdata`, artefacts dans
`mlartifacts`) : ne jamais l'utiliser sur la VM.

## 6. Contacts et escalade
- Binôme Demerges / Faucon / Sultana (dépôt `gdemerges/velov-demerges-faucon-sultana`), canal Teams du cours.
- VM, réseau de l'école, accès Proxmox : l'enseignant (Mohamed Gassem).

## 7. Pipeline de données (S3)

### La collecte s'est arrêtée
| Étape | Commande | Attendu |
|---|---|---|
| Le collecteur tourne | `docker compose --profile collect ps collector` | `Up` |
| Dernier relevé | `docker compose exec db psql -U velov -d velov -c "select max(observed_at), count(*) from bronze.station_status"` | heure UTC pleine récente |
| Cause | `docker compose logs --tail 50 collector` | une trace `Relevé grandlyon échoué` indique l'API ou le réseau |
| Relancer | `docker compose --profile collect up -d collector` | `restart: unless-stopped` le relance seul après un crash |

Un relevé manqué ne peut pas être récupéré (la source ne fournit que l'état courant) : le trou reste dans
bronze et gold n'y fabrique pas de cible. Relancer vite limite la perte ; chaque jour sans collecte manque à l'entraînement.

### Rejouer une journée
Le rejeu ne duplique rien (clé primaire station + heure, `ON CONFLICT DO NOTHING`) :
```bash
python -m velov.ingest --source simulator --start 2026-06-01 --days 1   # relecture d'une source rejouable
cd dbt && dbt build --profiles-dir .                                     # reconstruit silver et gold depuis bronze
```
Pour corriger des données déjà écrites en bronze, supprimer d'abord les lignes de la période
(`delete from bronze.station_status where observed_at >= '...' and observed_at < '...'`), puis relancer l'ingestion.
Vérifier ensuite l'idempotence : `./scripts/check_idempotence.sh`.

### `dbt build` échoue
| Symptôme | Cause probable | Action |
|---|---|---|
| `FAIL n value_between_...capacity` | relevés aberrants en bronze (n > 5) | `select * from silver.silver_station_status where capacity not between 1 and 80`, corriger ou purger la source, relancer |
| `Contract Error` sur gold | colonne ou type différent du contrat | comparer `dbt/models/gold/gold.yml` et `src/velov/contract.py` |
| `SKIP relation gold...` | un test silver est en échec en amont | gold garde sa dernière version valide ; traiter l'échec silver |
| `train --from-gold` : `SchemaErrors` | table gold hors contrat | l'entraînement s'arrête volontairement (EX-12) ; lire le détail des violations |
