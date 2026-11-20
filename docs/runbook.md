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

Depuis S6, l'API n'est joignable **que par le proxy, en HTTPS** (EX-19) : le port 8000 n'est plus publié.
Sur la VM, `.env` contient `COMPOSE_FILE=compose.yaml:compose.mlflow.yaml:compose.monitoring.yaml`, lu par toute
commande `docker compose` (`release.sh` compris). Le client fait confiance au certificat auto-signé avec
`--cacert certs/velov.crt` (copié depuis la VM ; la clé `certs/velov.key` n'en sort jamais).

Les cinq appels de contrôle, depuis le poste (`VM=192.168.1.234`) :
```bash
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" http://$VM/ready     # 301 https://192.168.1.234/ready
curl -sS https://$VM/ready                                                    # curl: (60) self-signed certificate
curl -s --cacert certs/velov.crt https://$VM/ready                            # {"status":"ready","model_version":"0.1.0"}
curl -s --cacert certs/velov.crt -o /dev/null -w "%{http_code}\n" https://$VM/metrics   # 404 : /metrics reste interne
# sur la VM : docker compose stop api, puis depuis le poste
curl -s --cacert certs/velov.crt -o /dev/null -w "%{http_code}\n" https://$VM/ready     # 502 : le proxy ne joint plus l'API
curl -s -m 3 http://$VM:8000/ready                                            # aucune réponse : port 8000 fermé
```

| Vérification | Commande | Résultat attendu |
|---|---|---|
| Le process répond | `curl -s --cacert certs/velov.crt https://<hôte>/health` | `{"status":"ok"}` |
| Le modèle est chargé | `curl -s --cacert certs/velov.crt https://<hôte>/ready` | HTTP 200 + version |
| Quel modèle tourne | `curl -s --cacert certs/velov.crt https://<hôte>/v1/model` | version, `registry.version`, métriques, données |
| Prometheus lit l'API | `http://<hôte>:9090/targets` | `velov-api` UP |
| Alertes | `http://<hôte>:9090/alerts` | toutes `inactive` en temps normal |
| Tableau de bord | `https://<hôte>/grafana/` (dossier Velov, « Vélo'v · API ») | débit, 5xx, SLI, modèle servi |
| Conteneurs sains | `docker compose ps` | `api` et `db` `Up (healthy)` |
| Une prédiction est journalisée | `docker compose exec db psql -U velov -d velov -c "select * from predictions order by id desc limit 3"` | lignes récentes, `created_at` en UTC |
| Temps passé dans l'API | `curl -s --cacert certs/velov.crt -D - -o /dev/null https://<hôte>/ready \| grep -i x-process-time` | quelques ms |
| Temps vu par le proxy | `docker compose logs --tail 20 proxy` | `rt=` (total) proche de `urt=` (API) |

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
3. Contrôler : `curl -s --cacert certs/velov.crt https://$VM/v1/model` -> `registry.version` est la nouvelle version.
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

### Enrichissements (référentiel, altitude, prévision météo)
Le collecteur les écrit après chaque relevé. Un échec est journalisé
(`Enrichissement « ... » échoué : relevé conservé`) et ne fait jamais perdre le relevé des stations.

| Vérification | Commande | Attendu |
|---|---|---|
| Référentiel et altitudes | `docker compose exec db psql -U velov -d velov -c "select count(*), count(elevation_m) from bronze.station_info"` | ~459 stations, toutes avec une altitude |
| Prévisions | `docker compose exec db psql -U velov -d velov -c "select max(issued_at), count(*) from bronze.weather_forecast"` | `issued_at` = heure du dernier relevé, 3 lignes par heure |
| Features | `cd dbt && dbt build --profiles-dir .` | `gold_features_hourly` construite, `features_no_lookahead` PASS |
| Build planifié | `crontab -l` ; `tail -5 logs/dbt_build.log` | `55 * * * * .../scripts/dbt_build.sh` ; une ligne `OK ... PASS=34` par heure |

`dbt build` tourne toutes les heures à :55 (crontab de la VM, `scripts/dbt_build.sh`). Une ligne `ÉCHEC` dans
`logs/dbt_build.log` renvoie au détail dans `logs/dbt_build.last.txt`. Gold garde alors sa dernière version valide
(tests bloquants) : on corrige la cause (section « `dbt build` échoue » ci-dessous), puis on relance le script à la main.
`SAUTÉ` signifie que le build précédent n'était pas fini : à surveiller s'il se répète.

Ajouter un événement (match, grève TCL, travaux) : une ligne par jour dans `dbt/seeds/events_lyon.csv`, **avec sa
source**, puis `dbt seed --profiles-dir .`. Mettre à jour les gares et universités : `python scripts/refresh_poi.py`,
puis relire le diff.

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

## 8. Performance et capacité (EX-16)

- **SLO** : 95 % des `POST /v1/predict` sous **200 ms**, moins de **1 %** d'erreurs.
- **Capacité mesurée** (1 worker, 1 cœur, ORT 1 thread, mesure locale, README section Performance) :
  **~42 req/s sous le SLO**, plafond **74 req/s** ; au-delà la latence explose (p95 610 ms à 100 usagers).
- **Près du plafond** (débit > 35 req/s par worker, ou p95 qui monte) :
  1. comparer `X-Process-Time-Ms` (temps dans l'API) à la latence vue par le client : s'ils sont proches, l'API calcule
     lentement (CPU, `docker stats`) ; si l'écart grandit, les requêtes attendent leur tour (file) ;
  2. vérifier que le conteneur n'est pas bridé : `docker exec <api> cat /sys/fs/cgroup/cpu.stat` (`nr_throttled`) ;
     `ORT_INTRA_OP_THREADS` doit valoir 1 ;
  3. ajouter un worker et un cœur (`compose.workers.yaml`, `docker update --cpus 2`) en surveillant la mémoire
     (~100 Mo par worker) ;
  4. la journalisation (75 % du temps d'une prédiction) est le prochain goulot : pool de connexions.

## 9. Remplacer le modèle : stratégie et retour arrière (EX-18)

Une seule VM, un seul conteneur API, pas de répartiteur de charge : un **canary** (x % du trafic vers le
nouveau modèle) demanderait un proxy qu'on n'a pas. On fait donc **shadow, puis bascule**, avec retour arrière
par l'image précédente.

| Phase | Ce qui tourne | Durée | On passe à la suite si |
|---|---|---|---|
| 0. Hors ligne | `velov.promote` : challenger, champion et persistance sur la même fenêtre (EX-14) | — | décision PROMOUVOIR |
| 1. Shadow | le challenger est livré dans une 2e image (`API_IMAGE=velov-api:m<v>-<commit>`, `API_PORT=8001`) ; un rejeu des requêtes de la veille (table `predictions`) lui est envoyé, ses réponses ne servent à personne | 24 h | MAE à H+1 du challenger <= MAE du champion sur les mêmes requêtes, 0 erreur 5xx, p95 sous le SLO (paliers de la section 8 sur le port 8001) |
| 2. Bascule | `./scripts/release.sh` (section 5) : le challenger devient l'API | — | `/v1/model` donne la nouvelle version |
| 3. Surveillance | le nouveau modèle sert seul ; l'ancienne image reste sur la VM (`docker image ls velov-api`) | 48 h | aucun seuil franchi |

**Signaux et seuils de retour arrière** (pendant la phase 3, on revient en arrière dès qu'un seuil est franchi, sans débat) :

| Signal | Où le lire | Seuil |
|---|---|---|
| `/ready` en 503 ou conteneur qui redémarre | `docker compose ps`, `curl /ready` | dès la première occurrence |
| Erreurs 5xx sur `/v1/predict` | `docker compose logs api` | > 1 % sur 15 min |
| Latence p95 | paliers de la section 8 (et Prometheus à partir de S6) | > 200 ms sous la charge habituelle |
| Erreur du modèle | MAE à H+1 : `predictions` jointes à `bronze.station_status` une heure après | > MAE du champion précédent + 10 % sur 24 h |
| Prédictions aberrantes | `select model_version, avg(predicted_bikes), count(*) from predictions group by 1` | moyenne qui s'écarte de plus de 30 % de la veille |

**Commande de retour arrière** (moins d'une minute, sans build ni registre) :
```bash
docker image ls velov-api                                               # l'image d'avant : m<ancienne version>-<commit>
API_IMAGE=velov-api:m<ancienne version>-<commit> docker compose up -d --no-build --wait api
curl -s --cacert certs/velov.crt https://$VM/v1/model                  # registry.version = l'ancienne version
python -m velov.promote --rollback                                      # puis remettre le registre d'accord (@champion)
```
Noter dans le registre la raison du retour arrière (tag `rollback` sur la version) et ouvrir un ticket :
un modèle retiré ne revient qu'avec une nouvelle décision `velov.promote`.

## 10. Alertes et surveillance du modèle (EX-20, EX-21)

Règles : `docker/prometheus/alerts.yml`, vérifiées par
`docker compose exec prometheus promtool check rules /etc/prometheus/alerts.yml` après chaque modification
(puis `docker compose restart prometheus`). État : `http://<hôte>:9090/alerts`.

| Alerte | Déclenche quand | Premier geste | Ensuite |
|---|---|---|---|
| `ApiInjoignable` | `up{job="velov-api"} == 0` pendant 1 min | `docker compose ps api`, `docker compose logs --tail 100 api` | `docker compose up -d api` ; si l'image est en cause : image précédente (section 5) |
| `LatenceHorsSLO` | moins de 95 % des `/v1/predict` sous 200 ms pendant 5 min | panneau « Débit par route » : le débit dépasse-t-il ~35 req/s ? | section 8, « près du plafond » ; si le débit est normal : `docker stats`, journaux du proxy (`rt` vs `urt`) |
| `Erreurs5xx` | plus de 1 % de 5xx sur `/v1/predict` pendant 5 min | `docker compose logs api \| grep -i error` | base indisponible ou modèle : section 4 ; après une livraison : retour arrière immédiat (section 9) |
| `LatenceBudgetBrule` | plus de 30 % de requêtes lentes sur 1 h **et** sur 5 min | comme `LatenceHorsSLO` | c'est le signal à traiter en premier : le budget du mois fond |

**Burn rate : facteur et fenêtres.** Le SLO tolère 5 % de requêtes lentes : c'est le budget d'erreur. Un facteur 6
signifie que le budget est consommé six fois trop vite (30 % de requêtes lentes) : à ce rythme, le budget d'une
semaine part en un peu plus d'une journée. On l'exige à la fois sur 1 h, pour ne pas alerter sur un pic, et sur
5 min, pour que l'alerte retombe dès que la cause est corrigée (la fenêtre longue, elle, mettrait une heure à
oublier). Un facteur plus bas (2 à 3, sur 6 h) attraperait les dégradations lentes, mais n'a pas de sens tant que
Prometheus ne garde que 15 jours et que le trafic de la VM est celui des tests. À revoir avec un trafic réel.

**Le modèle (`velov.monitor`).** Il se lance sur la VM, avec la base joignable en local :
```bash
pip install -r requirements-monitoring.txt && set -a && . ./.env && set +a
export DATABASE_URL=postgresql+psycopg://velov:$POSTGRES_PASSWORD@127.0.0.1:5432/velov
python -m velov.monitor --db "$DATABASE_URL" --data data/velov_history.csv --model-dir <dossier du modèle servi> --out reports/
```
`--model-dir` doit contenir le `model.onnx` et le `metadata.json` **servis** :
`docker compose cp api:/app/models/. models/served/`. `--data` correspond aux données d'entraînement indiquées par
`/v1/model` (champ `data`) ; le monitor prévient si l'empreinte diffère.
Code de sortie : 0 tout va bien, 1 alerte de performance, 2 rien à surveiller.

| Ce que dit le rapport | Qui | Conduite à tenir |
|---|---|---|
| Colonnes en dérive (score > 0,1), erreur stable | équipe data, dans la semaine | lire `reports/drift.html` : saisonnalité attendue (température) ou défaut de qualité (contrats S3) ? une note dans la model card ; **pas de réentraînement automatique** |
| Ratio global modèle / persistance > 0,90 | équipe data, sous 24 h | comparer à l'entraînement (0,71) ; réentraîner (`velov.retrain`), puis la règle de S4 décide (`velov.promote`) et un membre du binôme valide ; `release.sh` |
| Une station au-dessus de la persistance (ratio > 1,0, au moins 100 prédictions) | équipe data, sous 24 h | `reports/stations.csv` : les stations en défaut partagent-elles un profil (bureaux, quartier) ? changement d'usage (déménagement) : réentraîner sur des données récentes ; capteur ou station en panne : exclure la station et ouvrir un ticket |
| Code 2 (aucune prédiction) | astreinte | l'API ne journalise plus : section 4, « Prédictions non journalisées » |

Une dérive ne promeut jamais un modèle toute seule : la règle de S4 et un humain décident.

**Exécution quotidienne.** La crontab de la VM lance `scripts/monitor_daily.sh` chaque jour à 6 h 10 UTC. Il juge
le modèle que sert l'API à cet instant (copié depuis le conteneur), écrit le rapport dans `reports/<date>/` et
ajoute une ligne à `logs/monitor.log`.

| Ligne du journal | Code | Où d'autre | Conduite |
|---|---|---|---|
| `OK modèle …, ratio …` | 0 | — | rien |
| `ALERTE modèle …, ratio … : station …` | 1 | `journalctl -t velov-monitor` | tableau ci-dessus (ratio global ou stations) |
| `RIEN-À-JUGER` | 2 | — | l'API ne journalise plus, ou aucune vérité à H+1 : section 4 |
| `ERREUR` | 3 ou autre | `journalctl -t velov-monitor` | `logs/monitor.last.txt` ; conteneur `api` arrêté : `docker compose up -d api` |

Vérifié le 20/11 : semaine « déménagement » rejouée, le script sort en code 1, et l'alerte (7 stations) se retrouve
dans `logs/monitor.log` et dans `journalctl`.
