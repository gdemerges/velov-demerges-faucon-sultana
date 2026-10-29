# Model card : velov-availability

> Mise à jour à chaque nouvelle version promue. Inspirée de Mitchell et al., "Model Cards for Model Reporting" (2019).

## 1. Identité
- Nom / version : `velov-availability`, version 3 du registre MLflow (`@champion`), `model_version` 0.3.0
- Date d'entraînement : 2026-10-29 (run `71af0a0d`, commit `2a1bec7`, `git_dirty=false`)
- Responsables (binôme) : Demerges / Faucon / Sultana
- Emplacement de l'artefact : registre MLflow de la VM (`models:/velov-availability@champion`, paquet
  `package/` du run), puis image `velov-api:m3-<commit>` livrée par `scripts/release.sh` (ADR-0004)

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
- Dérive attendue : saison (température), travaux, nouvelles stations ; mesurée en S6
- Données personnelles : non (état agrégé des stations, aucune donnée d'usager)

## 7. Exploitation
- Latence cible : < 50 ms par prédiction (S5)
- Réentraînement : planifié chaque lundi à 6 h (crontab de la VM, `velov.retrain`), promotion validée par
  un membre du binôme (`velov.promote --apply`) ; règle : même fenêtre, bat la persistance, bat le champion de 2 %
- Livraison et retour arrière : [runbook](runbook.md), section 5
