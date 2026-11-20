# Backtest du monitoring (généré par `python -m velov.backtest`)

55 passages quotidiens sur l'historique non modifié ; scénarios injectés à chaque date de début possible, suivis 7 jours.

Demi-largeur médiane de l'IC à 95 % du ratio d'une station (bootstrap par blocs de 6 h) :

| n prédictions | 24 | 48 | 72 | 96 | 120 | 144 | 166 |
|---|---|---|---|---|---|---|---|
| demi-largeur | 0.256 | 0.183 | 0.150 | 0.127 | 0.114 | 0.101 | 0.097 |

| Fenêtre | Jeu de seuils | Global | Station | Min. n | Fausses alertes (global / station / total) | froid : en 48 h | froid : en 7 j | froid : délai médian | demenagement : en 48 h | demenagement : en 7 j | demenagement : délai médian |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 7 j | seuils TP6 | 0.90 | 1.00 | 100 | 0 % / 16 % / 16 % | 6 % | 44 % | 90 h | 6 % | 100 % | 102 h |
| 2 j | calibrés 2 j | 0.91 | 1.30 | 43 | 2 % / 2 % / 4 % | 4 % | 16 % | 54 h | 22 % | 86 % | 54 h |
| 3 j | calibrés 3 j | 0.87 | 1.19 | 64 | 0 % / 2 % / 2 % | 7 % | 29 % | 78 h | 14 % | 100 % | 54 h |
| 7 j | calibrés 7 j | 0.80 | 1.06 | 151 | 2 % / 0 % / 2 % | 0 % | 2 % | 126 h | 4 % | 100 % | 78 h |
