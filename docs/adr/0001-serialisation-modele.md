# ADR-0001 : Format de sérialisation du modèle v0

- Statut : remplacé par ADR-0003 pour le serving (joblib reste produit pour MLflow)
- Date : 2026-10-06
- Auteurs : équipe pédagogique (exemple d'ADR rempli)

## Contexte
Le modèle v0 est un pipeline scikit-learn (prétraitement + HistGradientBoostingRegressor).
Il doit être chargé par l'API. Les formats pickle/joblib exécutent du code au chargement.

## Options envisagées
| Option | Avantages | Inconvénients |
|---|---|---|
| joblib (pickle) | Natif sklearn, garde tout le pipeline, simple | Exécution de code au chargement, dépend des versions exactes des librairies |
| ONNX | Format portable, runtime rapide, pas d'exécution de code | Conversion à maintenir, tous les transformeurs ne sont pas supportés |
| skops | Chargement sans exécution de code arbitraire, garde le pipeline sklearn | Types à déclarer comme "trusted", écosystème plus jeune |

## Décision
joblib pour la v0, avec deux garde-fous : empreinte SHA-256 enregistrée dans `metadata.json`
et vérifiée par l'API avant chargement, versions des librairies épinglées et tracées.
Le journal MLflow utilise skops (format par défaut des modèles sklearn dans MLflow 3).

## Conséquences
- L'artefact n'est chargeable qu'avec les versions listées dans `metadata.json`.
- L'empreinte détecte une modification de l'artefact, pas un artefact malveillant produit en amont :
  la chaîne d'entraînement elle-même doit être de confiance.
- À réévaluer en S5 (optimisation : ONNX Runtime).
