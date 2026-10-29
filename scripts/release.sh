#!/usr/bin/env bash
# Livraison du modèle, sur la VM (là où le serveur MLflow est joignable, S4 · 27 et 34) :
# export de l'alias depuis le registre, build de l'image velov-api:m<version>-<commit>, redémarrage de l'API.
#
#   ./scripts/release.sh                      # livre @champion
#   ./scripts/release.sh previous-champion    # retour arrière par le registre (reconstruit l'image)
#
# Retour arrière le plus rapide : l'image précédente existe déjà sur la VM, on la relance sans build :
#   docker image ls velov-api                 # repérer le tag précédent, ex. m1-9e47a9a
#   API_IMAGE=velov-api:m1-9e47a9a docker compose up -d --no-build --wait api
set -euo pipefail
cd "$(dirname "$0")/.."

ALIAS="${1:-champion}"
export MLFLOW_TRACKING_URI="${MLFLOW_TRACKING_URI:-http://127.0.0.1:5000}"
PY="${PYTHON:-.venv/bin/python}"

# Le tag de l'image nomme un commit : il doit décrire exactement le code construit.
if ! git diff --quiet HEAD; then
    echo "Modifications non commitées : commiter (ou git stash) avant de livrer." >&2
    exit 1
fi

OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
"$PY" -m velov.export --alias "$ALIAS" --out "$OUT"
VERSION="$("$PY" -c 'import json, sys; print(json.load(open(sys.argv[1]))["registry"]["version"])' "$OUT/metadata.json")"
COMMIT="$(git rev-parse --short HEAD)"
IMAGE="velov-api:m${VERSION}-${COMMIT}"

docker build \
    --build-arg MODEL_FROM=export --build-context exported="$OUT" \
    --build-arg VERSION="m${VERSION}" --build-arg GIT_COMMIT="$COMMIT" \
    -t "$IMAGE" .
API_IMAGE="$IMAGE" docker compose up -d --no-build --wait api

echo "Livré : $IMAGE (@$ALIAS)"
curl -fsS "http://127.0.0.1:${API_PORT:-8000}/v1/model" | "$PY" -m json.tool | sed -n '/"registry"/,/}/p'
