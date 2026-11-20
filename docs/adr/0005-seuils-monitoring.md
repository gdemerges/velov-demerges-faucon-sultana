# ADR-0005 : fenêtre et seuils du monitoring du modèle, calibrés par backtest

- Statut : accepté
- Date : 2026-11-20
- Auteurs : binôme (extension « Monitoring ML planifié »)

## Contexte
`velov.monitor` tourne chaque jour à 6 h 10 UTC (crontab de la VM) et alerte sur Discord. Au TP6, sa fenêtre
(7 jours) et ses seuils (ratio global MAE modèle / persistance > 0,90 ; une station > 1,0 sur au moins
100 prédictions) ont été choisis à la main sur trois semaines rejouées. La question de l'extension : le monitor
prévient-il d'une vraie dégradation en moins de 48 h, sans alerter sur une semaine ordinaire ?

Mesure : `python -m velov.backtest` (résultats dans [docs/backtest/resultats.md](../backtest/resultats.md), runs
dans l'expérience MLflow `velov-monitoring-backtest`). 55 passages quotidiens rejoués sur l'historique (modèle
réentraîné chaque semaine sur le seul passé) ; scénarios froid et déménagement injectés à chacune des 49 dates
de début possibles et suivis 7 jours. Seuils calibrés = quantile 99 % des passages normaux ; le seuil par station
porte sur la **pire** station du passage (20 stations jugées à chaque fois).

## Options envisagées
| Option | Fausses alertes | Déménagement : détecté en 7 j / en 48 h / délai médian | Inconvénients |
|---|---|---|---|
| A. Seuils TP6, fenêtre 7 j | **16 %** des passages | 100 % / 6 % / 102 h | une alerte par semaine sans cause : on finit par l'ignorer |
| B. Calibrés, fenêtre 7 j (0,80 / 1,06 / 151) | 2 % | 100 % / 4 % / 78 h | l'incident est dilué dans 7 jours |
| C. Calibrés, fenêtre 3 j (0,87 / 1,19 / 64) | 2 % | 100 % / 14 % / **54 h** | ratio par station plus bruité (IC ±0,15 au lieu de ±0,10) |
| D. Calibrés, fenêtre 2 j (0,91 / 1,30 / 43) | 4 % | 86 % / 22 % / 54 h | rate 14 % des déménagements, deux fois plus de fausses alertes |

## Décision
Nous retenons **C** : fenêtre de 3 jours, ratio global > 0,87, pire station > 1,19 sur au moins 64 prédictions
(constantes de `velov/monitor.py`). Même taux de fausses alertes que B, détection systématique, et un jour de
moins pour détecter. D ne détecte pas plus vite en médiane (54 h aussi) et en manque.

Pourquoi ces nombres :
- fausses alertes : 2 % des passages, soit environ une par mois et demi, contre plus d'une par semaine au TP6 ;
- 64 prédictions = 90 % des 72 h de la fenêtre : une station à moitié muette n'est pas jugée ;
- 1,19 et non 1,0 : sur 3 jours, une station normale dépasse la persistance par simple bruit (IC à 95 % du
  ratio : ±0,15 à 72 prédictions, bootstrap par blocs de 6 h) ;
- le froid (−15 °C) n'est détecté qu'à 29 % : c'est voulu, le modèle s'y dégrade peu (ratio ~0,78). La dérive
  est lue dans `drift.html`, elle ne réveille personne.

## Conséquences
- **La réponse à la question est non** : avec un passage par jour, le délai médian est de 54 h, et seuls 14 % des
  déménagements sont vus en 48 h. Les passages tombent à D + 6 h, D + 30 h, D + 54 h : la détection se joue au
  troisième. Pour passer sous 48 h, il faut un monitor plus fréquent (toutes les 6 h), pas d'autres seuils :
  c'est la mesure prévue en S8 (scénario rejoué heure par heure). Un passage plus fréquent multiplie les
  occasions de fausse alerte : à recalibrer sur cette cadence avant de l'adopter.
- Seuils calibrés sur le simulateur (20 stations, 90 jours). Sur les vraies stations (459, collecte depuis le
  20/11), relancer `velov.backtest` quand quelques semaines seront disponibles : le bruit par station et le
  nombre de stations changent tous deux le seuil de la pire station.
- Les scénarios conçus (capteur bloqué, chantier) s'ajoutent à `velov.backtest.SCENARIOS` : le tableau dira si C
  les attrape.
- Les quantiles 99 % reposent sur 55 passages qui se chevauchent : ce sont en pratique les maximums observés.
  Un taux de 2 % = 1 passage sur 55.
