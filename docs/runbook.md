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
1.
2.
3. Retour arrière (rollback) :

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
