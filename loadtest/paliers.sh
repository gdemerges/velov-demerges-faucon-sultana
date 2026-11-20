#!/usr/bin/env bash
# Courbe de saturation : un échauffement non compté, puis un palier par nombre d'usagers.
# À lancer DEPUIS VOTRE POSTE, vers l'API sur la VM.
#
#   ./loadtest/paliers.sh http://192.168.1.2xx:8000 w1
#   python loadtest/summary.py results/w1
#
# Arguments : URL de l'API, préfixe des résultats (une configuration = un préfixe : w1, w2, onnx...).
# Variables : PALIERS (défaut "10 25 50 100 150"), DUREE par palier (défaut 2m), WARMUP (défaut 1m).
set -euo pipefail

HOST="${1:?usage : paliers.sh <url_api> <prefixe>}"
PREFIX="${2:?usage : paliers.sh <url_api> <prefixe>}"
PALIERS="${PALIERS:-10 25 50 100 150}"
DUREE="${DUREE:-2m}"
WARMUP="${WARMUP:-1m}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="results/${PREFIX}"
mkdir -p "$OUT"

curl -fsS "${HOST}/ready" > /dev/null || { echo "L'API ne répond pas sur ${HOST}/ready" >&2; exit 1; }

# Ce qu'il faut pour comparer deux mesures plus tard (règle 6 du protocole).
{
  echo "date_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "host=${HOST}"
  echo "paliers=${PALIERS} duree=${DUREE} warmup=${WARMUP}"
  echo "commit=$(git rev-parse --short HEAD 2>/dev/null || echo inconnu)"
  echo "poste_coeurs=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo inconnu)"
  echo "locust=$(locust --version 2>/dev/null | head -1)"
  echo "model=$(curl -fsS "${HOST}/ready")"
} > "${OUT}/protocole.txt"

echo "Échauffement (${WARMUP}, non compté)"
locust -f "${HERE}/locustfile.py" --host "$HOST" --headless -u 10 -r 10 -t "$WARMUP" --only-summary > /dev/null 2>&1

for u in $PALIERS; do
  r=$(( u < 20 ? u : u / 5 ))   # montée en 5 s environ
  echo "Palier ${u} usagers (${DUREE})"
  locust -f "${HERE}/locustfile.py" --host "$HOST" --headless -u "$u" -r "$r" -t "$DUREE" \
    --csv "${OUT}/u${u}" --only-summary > "${OUT}/u${u}.log" 2>&1 || true
done

echo "Résultats dans ${OUT}/ ; résumé : python loadtest/summary.py ${OUT}"
