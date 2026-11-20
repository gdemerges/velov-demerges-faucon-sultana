# Model card : velov-availability

> Mise à jour à chaque nouvelle version promue. Inspirée de Mitchell et al., "Model Cards for Model Reporting" (2019).

## 1. Identité
- Nom / version : `velov-availability`, `model_version` 0.1.0, servi sur la VM depuis le TP6 (20/11/2026) ;
  hors registre (`registry` vide dans `/v1/model`)
- Date d'entraînement : 2026-11-20 09:08 UTC, pendant le build de l'image (`MODEL_FROM=train`, ADR-0004) :
  simulateur 90 jours, seed 42, empreinte des données `4a861c99`, SHA-256 de l'artefact `b706545f`
- Responsables (binôme) : Demerges / Faucon / Sultana
- Emplacement de l'artefact : dans l'image `velov-api:0.1.0-21a1b81` (`/app/models/model.onnx` + `metadata.json`),
  copié pour le monitoring dans `~/velov-mlops/models/served/` (runbook, section 10)
- Historique : jusqu'au TP5, le modèle servi était la version 3 du registre MLflow (`@champion`, `model_version`
  0.3.0, run `71af0a0d`, commit `2a1bec7`), livrée par `scripts/release.sh`. Le dépôt a été réinstallé sur la VM
  au TP6 et son registre MLflow est vide : ce modèle n'est plus servi. Les sections 3 et 4 décrivent encore la v3.

## 2. Usage prévu
- Ce que le modèle prédit : le nombre de vélos disponibles dans une heure, par station Vélo'v
- Utilisateurs et décisions alimentées : application qui indique à l'usager s'il trouvera un vélo à H+1
- Usages hors périmètre : prévision au-delà d'une heure, rééquilibrage logistique, stations jamais vues
  (l'encodage les ignore : la prédiction se rabat sur les autres features)

## 3. Données
- Source, période, volume : simulateur (`velov.data`, 140 jours, 20 stations, 67 200 lignes,
  2026-05-31 -> 2026-10-18 UTC, empreinte du contenu `e51e9a3d`) ; la table gold réelle prend le relais
  quand la collecte couvre largement plus que les 14 jours de test
- Découpage : temporel, test sur les 14 derniers jours (on prédit le futur ; un découpage aléatoire ferait
  fuiter l'autocorrélation horaire)
- Limites connues : données simulées (profils résidentiel / bureau / loisir), météo commune à la ville,
  aucun événement exceptionnel (grève, travaux)

## 4. Performance
Mesurée par `velov.promote` sur la fenêtre la plus récente (2026-10-04 21:00 -> 2026-10-18 20:00 UTC, 6 720 lignes).

| Métrique | Modèle (v3) | Champion précédent (v1) | Baseline (persistance) | Seuil d'acceptation |
|---|---|---|---|---|
| MAE (vélos) | 1,371 | 1,506 | 1,926 | < persistance et <= champion - 2 % |
| MAE heures de pointe (7-9 h, 17-19 h, Lyon) | 1,458 | 1,672 | - | suivie, non bloquante |

- Bruit lié à la seed (90 jours, même code, mêmes données) : seed 42 -> 1,339, seed 7 -> 1,345 ; de 0 à 3 :
  1,339 à 1,360 (cours S4), soit jusqu'à 1,6 %. Le seuil de 2 % de la règle de promotion se place au-dessus.
- Le champion v1 faisait 1,339 à son entraînement et 1,506 sur la fenêtre récente (+12 %) : il a vieilli
  (températures jamais vues, un modèle à arbres extrapole mal).

## 5. Contrat d'entrée / sortie
- Entrées : `station_id` (>= 1), `timestamp` avec fuseau, `capacity` (1-100), `bikes_available` (0-capacity),
  `temperature` (-30 à 50 °C), `is_raining` ; champ inconnu refusé (422)
- Sortie : `predicted_bikes` en vélos, bornée à [0, capacity], `target_timestamp` en UTC, `model_version`
- Nouvelle station : pas d'erreur, la station est ignorée par l'encodage one-hot

## 6. Risques et limites
- Dérive attendue : saison (température), travaux, nouvelles stations ; mesurée en S6 (section 8)
- Données personnelles : non (état agrégé des stations, aucune donnée d'usager)

## 7. Exploitation
- Latence cible : < 50 ms par prédiction (S5)
- Réentraînement : planifié chaque lundi à 6 h (crontab de la VM, `velov.retrain`), promotion validée par
  un membre du binôme (`velov.promote --apply`) ; règle : même fenêtre, bat la persistance, bat le champion de 2 %
- Livraison et retour arrière : [runbook](runbook.md), section 5

## 8. Monitoring (S6, EX-21)

`velov.monitor` compare deux fenêtres de 7 jours :
- la **référence** : la première semaine de la fenêtre de test du modèle servi, avec ses prédictions ;
- la **semaine courante** : les 7 derniers jours de la table `predictions`, une ligne par station et par heure.

Il juge chaque prédiction faite à t avec l'observation de t + 1 h.

Modèle mesuré : celui qu'embarque l'image du TP6, `model_version` 0.1.0. Il est entraîné pendant le build sur le
simulateur (90 jours, seed 42), et ses données ont l'empreinte `4a861c99`, identique à `data/velov_history.csv`.
À l'entraînement, son ratio modèle / persistance vaut **0,71** (1,339 / 1,879). Trois semaines ont été rejouées
avec `velov.replay`, 3 360 prédictions chacune, dont 3 340 ont leur vérité (la dernière heure attend encore la
suivante).

| Semaine rejouée | Colonnes en dérive (score, seuil 0,1) | MAE modèle | MAE persistance | Ratio | Alerte |
|---|---|---|---|---|---|
| Normale | `temperature` (0,39) | 1,309 | 1,821 | 0,72 | non |
| Froid (−15 °C) | `temperature` (4,44) ; `predicted_bikes` 0,09 | 1,419 | 1,821 | 0,78 | non |
| Déménagement (stations de bureaux à partir du 3e jour) | `temperature` (0,39), `bikes_available` (0,25), `occupancy_rate` (0,30), `predicted_bikes` (0,26) | 1,584 | 1,818 | 0,87 | **oui** : 7 stations |

Mesuré le 20/11/2026 sur la VM (`velov.replay` vers `https://192.168.1.234`, puis `velov.monitor` sur la VM ;
rapports dans `~/velov-mlops/reports/<scénario>/`), et en local sur la même stack : chiffres identiques entre
les deux, et identiques à ceux du cours (S6 · 22).

**1. Quelles colonnes dérivent, et pourquoi ?**
- `temperature` dérive dans les trois semaines, même la normale : la référence est la première semaine de test
  (mi-août), la semaine rejouée est la dernière (fin août), environ 1,5 °C plus fraîche. C'est de la saisonnalité,
  pas une panne : l'erreur ne bouge pas (0,72 contre 0,71).
- Le froid fait exploser le score de `temperature` (4,44), mais les prédictions bougent peu (0,09) : le modèle
  s'appuie peu sur la température.
- Le déménagement fait dériver les entrées liées à l'occupation et la sortie du modèle (0,25 à 0,30), avec une
  température normale : c'est l'usage des stations qui a changé.

**2. Le ratio à la persistance, comparé à celui de l'entraînement.**
- Semaine normale : 0,72, au niveau de l'entraînement (0,71).
- Froid : 0,78, une perte modérée pour une température jamais vue.
- Déménagement : 0,87. C'est la seule semaine qui coûte vraiment, alors que ce n'est pas celle où la dérive est
  la plus forte.

**3. Les stations les plus mal prédites : un segment qui décroche ?**
Oui, dans la semaine de déménagement. Les 7 stations au-dessus de la persistance (ratio > 1,0) sont exactement
les stations de bureaux déplacées : 2, 5, 8, 11, 14, 17, 20. Sur la station 17, le modèle fait 2,85 contre 2,79
pour la persistance ; sur la station 14, 2,77 contre 2,74. Le ratio global (0,87) reste sous le seuil et ne
voit rien : c'est le suivi par station qui alerte. Dans les semaines normale et froid, les stations 14 et 17 sont
aussi les plus mal prédites (autour de 1,8 à 2,0), mais elles restent bien sous la persistance (ratio 0,62 à 0,69).

**4. Réentraîner ou non, et le seuil d'alerte retenu.**
- Normale et froid : **on ne réentraîne pas**. La dérive est lue, notée ici, et le ratio reste à moins de
  0,07 de l'entraînement.
- Déménagement : **on réentraîne** sur des données qui contiennent le nouveau comportement (`velov.retrain`).
  La promotion suit la règle de S4 (`velov.promote`, validée par un membre du binôme), sans automatisme.
- **Seuils retenus** :
  - ratio global > **0,90**. C'est 0,19 au-dessus de l'entraînement : il laisse passer les semaines
    ordinaires (0,72 à 0,78) et se déclenche quand le modèle a perdu plus de la moitié de son avance sur la
    persistance ;
  - par station, modèle pire que la persistance (ratio > **1,0**) sur au moins **100** prédictions (environ
    4 jours). En dessous de 100 prédictions, la MAE d'une station est trop bruitée pour conclure.
- Ces seuils sont choisis sur trois semaines seulement. Leur calibration par backtest (taux de fausses alertes,
  délai de détection) est l'objet de l'extension ([extension.md](extension.md)).
- On n'alerte pas sur la dérive : la semaine normale dérive déjà.

