# ADR-0003 : Servir le modèle au format ONNX

- Statut : accepté (remplace la décision de serving de l'ADR-0001)
- Date : 2026-10-26
- Auteurs : binôme Demerges / Faucon / Sultana

## Contexte
L'image de l'API pesait 345 Mo, dont 119 Mo pour scikit-learn, SciPy et joblib, qui ne servaient qu'à
charger le pipeline `model.joblib`. Le format pickle exécute du code au chargement (ADR-0001) et lie le
modèle aux versions exactes de scikit-learn.

## Options envisagées
| Option | Avantages | Inconvénients |
|---|---|---|
| A. Garder joblib | Rien à changer | Image lourde, exécution de code au chargement |
| B. Base Alpine | ~-60 Mo sans toucher au modèle | Roues musl moins éprouvées, scan à refaire |
| C. Export ONNX, serving par onnxruntime | Image sans scikit-learn ni SciPy ; aucun code exécuté au chargement ; runtime C++ rapide | Conversion à vérifier ; onnxruntime pèse lui-même 63 Mo ; un convertisseur de plus à maintenir |

## Décision
Option C. À l'entraînement, `velov.train` exporte le pipeline complet (OneHotEncoder + HistGradientBoosting)
avec skl2onnx (opset 17, ai.onnx.ml 3) et **compare** ses prédictions à celles de scikit-learn sur le jeu de
test : au-delà de 1e-3 vélo d'écart, l'entraînement échoue. Mesuré : ~1e-5 vélo (seuils en float32).
L'API charge `model.onnx` avec onnxruntime, après vérification de l'empreinte SHA-256 comme avant.
`model.joblib` reste produit pour MLflow et l'analyse, mais n'entre pas dans l'image.

Dépendances séparées : `requirements.txt` (serving, image), `requirements-train.txt` (+ scikit-learn,
skl2onnx, onnx ; étape builder du Dockerfile uniquement).

## Conséquences
- Image : **345 → 289 Mo** (-16 %). Gain net plus faible que les 119 Mo retirés, car onnxruntime ajoute 63 Mo.
- Le test `test_serving_does_not_import_sklearn` garantit que l'API et le collecteur ne rechargent pas
  scikit-learn ; `test_artifact_is_onnx_and_faithful` vérifie l'écart enregistré dans `metadata.json`.
- `protobuf` est épinglé (6.33.6) : à partir de 7.34, il rejette des attributs que skl2onnx 1.20 produit encore.
- Tout nouvel estimateur doit avoir un convertisseur skl2onnx ; l'export échoue sinon, à l'entraînement et non en production.
- Prochaine étape possible (S5) : quantification ou build minimal d'onnxruntime pour réduire encore la taille.
