# ADR-0004 : D'où l'API tire son modèle

- Statut : accepté
- Date : 2026-10-29
- Auteurs : binôme Demerges / Faucon / Sultana

## Contexte
Depuis S4, le registre MLflow (sur la VM) désigne par l'alias `champion` la version du modèle à servir.
L'image de l'API ne contient aucune dépendance MLflow (ni scikit-learn : ADR-0003) et doit démarrer même si
le serveur MLflow est arrêté. Le runner de la CI GitHub ne joint pas le réseau de l'école, donc pas le registre.

## Options envisagées
| Option | Avantages | Inconvénients |
|---|---|---|
| A. Charger `models:/velov-availability@champion` au démarrage | Changer de modèle sans rebuild | MLflow (et scikit-learn) dans l'image ; API bloquée si le registre tombe ; la production change sans déploiement |
| B. Recharger à chaud quand l'alias bouge | Pas de redémarrage | Deux modèles dans un même process, état difficile à tracer |
| C. Résoudre l'alias avant le build, copier le modèle dans l'image | Ce qui tourne est exactement ce qui a été testé ; registre indisponible sans effet sur l'API | Changer de modèle demande un build |

## Décision
Option C. `scripts/release.sh` (sur la VM) lance `velov.export --alias champion` : le paquet du run
(model.onnx + metadata.json) est téléchargé, son empreinte SHA-256 vérifiée, puis la version du registre est
inscrite dans `metadata.json` (bloc `registry`). L'image est construite avec ce paquet
(`--build-arg MODEL_FROM=export --build-context exported=<dossier>`) et taguée `velov-api:m<version>-<commit>`,
jamais `latest`. Sans ces options, le Dockerfile entraîne un modèle de test (CI, clone propre).

## Conséquences
- `GET /v1/model` renvoie la version du registre, le run, les paramètres et l'empreinte des données : depuis
  l'API, on remonte au run, au commit et aux données (EX-13).
- Revenir en arrière : relancer l'image précédente, déjà présente sur la VM
  (`API_IMAGE=velov-api:m1-<commit> docker compose up -d --no-build api`, quelques secondes),
  ou reconstruire depuis le registre (`./scripts/release.sh previous-champion`).
- Promouvoir dans le registre ne change rien en production tant que `release.sh` n'a pas tourné : la
  promotion et la livraison sont deux décisions tracées séparément (tag `promotion` sur la version, tag d'image).
- À revoir si l'on réentraîne plusieurs fois par jour ou si l'on sert de nombreux modèles (option A).
