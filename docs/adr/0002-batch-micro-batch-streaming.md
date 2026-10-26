# ADR-0002 : Batch, micro-batch ou streaming pour l'ingestion

- Statut : accepté
- Date : 2026-10-26
- Auteurs : binôme Demergès / Faucon / Sultana

## Contexte
Le service prédit le nombre de vélos disponibles **dans une heure**, par station. La source
(open data Métropole de Lyon) expose l'état d'environ 460 stations, rafraîchi à la minute.
Le modèle consomme une ligne par station et par heure ; l'entraînement se fait au mieux une fois par jour.
Ordre de grandeur : 460 stations x 24 h x 365 j ≈ 4 millions de lignes par an, soit moins de 1 Go en base.

## Options envisagées
| Option | Principe | Coût d'ordre de grandeur | Pour | Contre |
|---|---|---|---|---|
| Batch quotidien | un relevé par jour | ~0 (cron sur la VM existante) | Le plus simple | Un trou d'une journée perd 24 points par station ; incompatible avec une cible à H+1 |
| **Micro-batch horaire** | un relevé complet toutes les heures, écrit en bronze | ~0 (un processus de quelques Mo, 1 requête HTTP par heure) | Aligné sur la granularité du modèle, rejouable, un échec ne perd qu'une heure | Pas de fraîcheur infra-horaire |
| Streaming (Redpanda/Kafka + consommateur) | un événement par changement d'état, en continu | broker toujours allumé : ~1 à 2 Go de RAM et ~20 à 40 € par mois en cloud, plus l'exploitation | Fraîcheur à la seconde | Aucun besoin métier : l'horizon est une heure |

## Décision
Micro-batch horaire : le service `collector` du compose prend un relevé à l'heure UTC pleine et l'écrit
dans `bronze.station_status` avec une clé primaire (station, heure). Le traitement aval (dbt) est un batch
rejouable sur toute la période.

## Conséquences
- Rejouer est sans danger : `ON CONFLICT DO NOTHING` côté bronze, tables silver et gold reconstruites entièrement (EX-10).
- Un arrêt de la collecte laisse un trou ; la cible `bikes_next_hour` n'est alors pas fabriquée pour
  l'heure qui précède le trou (voir `gold_training_hourly`). Le runbook décrit le diagnostic.
- Si le besoin devenait « alerter quand une station se vide dans les 10 minutes », il faudrait réévaluer
  le streaming : c'est le seul scénario où son coût se justifie.
- Le stretch Redpanda du TP3 n'est pas retenu : il ajouterait un composant sans bénéfice mesurable ici.
