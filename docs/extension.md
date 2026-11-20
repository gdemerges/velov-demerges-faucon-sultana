# Extension : Monitoring ML planifié · niveau 3

## La question

Le monitoring quotidien du modèle prévient-il d'une **vraie** dégradation (globale ou sur un segment de
stations) en moins de 48 h, sans alerter sur une semaine ordinaire ?

Au TP6, `velov.monitor` utilise des seuils fixés à la main : ratio global modèle / persistance > 0,90, et
par station modèle > persistance (ratio > 1,0, sur au moins 100 prédictions). Sur trois semaines rejouées,
ils ont bien réagi : semaine normale 0,72, froid 0,78, déménagement 0,87 au global avec 7 stations
au-dessus de 1,0. Mais trois semaines ne permettent pas de savoir combien de fausses alertes ces seuils
produisent, ni avec quel délai ils détectent une dégradation.

## La mesure

- **Métriques** : taux de fausses alertes (semaines normales qui déclenchent), taux de détection (scénarios
  dégradés repérés) et délai de détection (heures entre le début de la dégradation et le premier run qui
  sort en code 1). Chacune est mesurée au global et par station.
- **Références** :
  - les seuils fixes du TP6 (0,90 et 1,0), comparés aux seuils calibrés ;
  - la persistance, qui sert de plancher : une station où le modèle fait pire que « rien » est en défaut.
- **Protocole (backtest)** :
  1. **Historique** : semaines glissantes de 7 jours, décalées d'un jour, sur la partie de l'historique
     postérieure à l'entraînement. Le modèle est réentraîné sur ce qui précède chaque fenêtre (même code que
     `velov.train`), pour ne jamais tester sur des données vues. Pour chaque fenêtre, on calcule le ratio
     global et le ratio par station avec les fonctions de `velov.monitor` (`performance`, `alerts`).
  2. **Calibration** : le seuil global et le seuil par station sont pris au quantile 99 % des semaines
     normales, et le minimum de prédictions par station est fixé pour que l'intervalle de confiance
     (bootstrap) du ratio reste sous ±0,1.
  3. **Scénarios injectés** dans `velov.replay` : froid et déménagement (fournis), et deux scénarios conçus :
     - capteur bloqué : `bikes_available` figé sur 3 stations pendant 24 h ;
     - chantier : capacité divisée par deux sur un quartier à partir du jour 3.

     Chaque scénario est mesuré avec les seuils fixes puis avec les seuils calibrés, en exécution quotidienne.
  4. **Résultat** : un tableau scénario × seuils (détection, délai, fausses alertes), et les seuils retenus
     dans `velov/monitor.py`, justifiés dans un ADR (ADR-0005).

## Où ça tourne

- **En local** : le backtest (`scripts/backtest_monitor.py`) lit le CSV et calcule les prédictions avec le
  graphe ONNX, sans passer par l'API ni par la base. Les tests pytest couvrent la calibration et les
  scénarios conçus.
- **Sur la VM, en S7** : `velov.monitor` lancé chaque jour par la crontab. Un code de sortie 1 laisse une trace
  qu'on retrouve : une ligne dans `reports/monitor.log` et un fichier `reports/<date>/` avec `drift.html`,
  `monitor.json` et `stations.csv`. On rejoue ensuite un scénario conçu pour vérifier qu'il est détecté.
- **Sur la VM, en S8** : on mesure le délai de détection réel sur un scénario rejoué heure par heure
  (`--pause`), avec le monitor planifié toutes les heures pendant la séance.

## Qui fait quoi

Répartition à confirmer en binôme avant l'entretien :

- A : backtest et calibration (étapes 1 et 2), ADR-0005 ;
- B : scénarios conçus dans `velov.replay` et leurs tests (étape 3), crontab et trace sur la VM ;
- ensemble : lecture des résultats, section Monitoring de la model card, conduite à tenir dans le runbook.

## Si ça ne marche pas

Le périmètre réduit qu'on livre quand même :

- le seuil global calibré par backtest, avec le seuil par station du TP6 conservé ;
- un seul scénario conçu (capteur bloqué) ;
- la crontab quotidienne avec sa trace.

La mesure qui tranche reste celle de l'étape 4, faite sur ce périmètre : fausses alertes et détection, seuils
fixes contre seuils calibrés.
