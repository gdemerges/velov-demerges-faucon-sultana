# Runbook : API velov-availability

> Template à compléter au fil des sessions. Public cible : une personne d'astreinte
> qui ne connaît pas le projet et doit rétablir le service à 3 h du matin.

## 1. Vue d'ensemble
- Ce que fait le service, en deux phrases :
- Schéma d'architecture (lien) :
- Dépendances (base de données, stockage du modèle...) :

## 2. Démarrer / arrêter
```bash
# démarrer
# arrêter
# redémarrer un seul service
```

## 3. Vérifier que tout va bien
| Vérification | Commande | Résultat attendu |
|---|---|---|
| Le process répond | `curl -s http://<hôte>:8000/health` | `{"status":"ok"}` |
| Le modèle est chargé | `curl -s http://<hôte>:8000/ready` | HTTP 200 + version |
| | | |

## 4. Incidents connus
| Symptôme | Cause probable | Diagnostic | Action |
|---|---|---|---|
| `/ready` renvoie 503 | | | |
| Le conteneur redémarre en boucle | | | |
| Latence élevée | | | |

## 5. Déployer une nouvelle version du modèle
Sur la VM (le serveur MLflow y est joignable, `MLFLOW_TRACKING_URI=http://127.0.0.1:5000`) :
1. Vérifier la décision : `python -m velov.promote --from-gold` (challenger, champion et persistance sur la
   même fenêtre). Si elle dit PROMOUVOIR et qu'on est d'accord : `python -m velov.promote --from-gold --apply`
   (l'ancien champion prend l'alias `previous-champion`, la raison est taguée sur la version).
2. Livrer : `git pull origin tp4 && ./scripts/release.sh` (export de `@champion`, image
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
-

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
