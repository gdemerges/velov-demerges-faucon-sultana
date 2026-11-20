# Audit : un test de charge écrit par une IA (S5, IA imposée)

Fichier audité : [`locustfile_ia.py`](locustfile_ia.py). Demande faite à l'assistant : « écris un test de charge
Locust pour mon API FastAPI de prédiction (POST /v1/predict, GET /health) et dis-moi si elle tient un SLO de 200 ms ».

## 1-2. Le verdict du script, comparé à la capacité mesurée

Pas encore lancé (manque de temps). Capacité mesurée à l'étape 2 : ~42 req/s sous le SLO, 74 req/s au plafond.

## 3. Les six défauts

| # | Ligne | Défaut | Effet sur le verdict | Correction |
|---|---|---|---|---|
| 1 | 15 `"timestamp": "2026-11-17T08:00:00"` | Timestamp **sans fuseau** : notre contrat le refuse (EX-01, 422). L'assistant ne connaissait pas le contrat. | 100 % des prédictions sont des 422 renvoyés par Pydantic avant le modèle et la base : on mesure le chemin d'erreur, le plus court de l'API. | Fuseau explicite (`...+00:00`), et un payload construit comme le client réel. |
| 2 | 29-31 `catch_response=True` + `response.success()` | Toute réponse est déclarée réussie, quel que soit son code. « Éviter que des erreurs faussent les statistiques », c'est cacher les erreurs. | 0 % d'erreurs affiché alors qu'aucune prédiction n'a réussi ; le critère « moins de 1 % d'erreurs » du SLO ne peut plus échouer. | Vérifier `status_code == 200` **et** le corps (`model_version`, `0 <= predicted_bikes <= capacity`), sinon `response.failure(...)`. |
| 3 | 13-20 `PAYLOAD` unique | Une seule station, un seul instant, un seul remplissage pour toutes les requêtes. | Un seul chemin dans les arbres, des caches toujours chauds : on mesure un cas, pas le service. | Tirer station, instant, vélos et météo au hasard dans des bornes plausibles (`loadtest/locustfile.py`). |
| 4 | 25 `wait_time = constant(0)` | Aucun temps de réflexion : chaque usager renvoie dès la réponse reçue. | « 100 usagers » ne veut plus rien dire (ce n'est pas le trafic de 100 personnes), et le débit dépend du client ; le verdict ne dit pas combien d'usagers l'API tient. | `between(0.5, 1.5)` comme l'application, et des **paliers** (10 à 150 usagers) plutôt qu'un seul point. |
| 5 | 33-35 `health` au même poids, et 40 `stats.total` | `/health` (gratuit) a le même poids que `/v1/predict`, et le verdict lit la ligne « total » qui mélange les deux routes. | La moitié des requêtes mesurées ne touche ni le modèle ni la base : la latence affichée est embellie. | Poids 10:1 (`@task(10)` / `@task(1)`), et juger sur `environment.stats.get("/v1/predict", "POST")` seulement. |
| 6 | 40-45 `avg_response_time`, `avg < 200` | Le SLO porte sur le **p95** (et sur le taux d'erreurs) ; la moyenne cache la queue de distribution. Le script n'échoue jamais (`process_exit_code` jamais posé). | Une API dont 10 % des requêtes prennent 1 s peut avoir une moyenne sous 200 ms et être déclarée conforme. | `stats.get_response_time_percentile(0.95) <= 200` **et** `fail_ratio < 0.01`, sinon `environment.process_exit_code = 1`. |

Défauts en plus, hors des six : `host` écrit en dur (ligne 24, `localhost` : le test part de la machine qui
sert l'API, règle 1 du protocole), une durée de 20 s sans échauffement (docstring), aucun protocole noté
(machine, cœurs, commit, version du modèle).

## 4. La demande réécrite

> Écris un test de charge Locust pour l'API de prédiction Vélo'v. Contrat d'entrée de `POST /v1/predict` :
> `station_id` (entier >= 1), `timestamp` ISO 8601 **avec fuseau** (sinon 422), `capacity` (1 à 100),
> `bikes_available` (0 à capacity), `temperature` (-30 à 50), `is_raining` (booléen) ; aucun autre champ.
> Réponse 200 : `predicted_bikes` entre 0 et capacity, `model_version` non vide.
> - Chaque requête doit être différente et plausible (station, instant, remplissage, météo tirés au hasard).
> - Une requête n'est réussie que si le code est 200 et que le corps respecte le contrat ; sinon `failure`.
> - Temps de réflexion de 0,5 à 1,5 s par usager ; `/health` dix fois moins souvent que `/v1/predict`.
> - Le SLO : sur `/v1/predict` seul, p95 <= 200 ms ET moins de 1 % d'erreurs ; code de sortie non nul sinon.
> - Pas de `host` dans le code (passé par `--host`) : le test tournera depuis une autre machine que l'API.
> - Ne déclare jamais le SLO tenu sur un seul nombre d'usagers : dis quels paliers lancer (10 à 150, 2 min chacun,
>   1 min d'échauffement non compté) et ce qu'il faut noter (machine, cœurs, commit, version du modèle).

## Ce qu'en dit un assistant quand on lui demande de relire son propre script

À faire en binôme.
