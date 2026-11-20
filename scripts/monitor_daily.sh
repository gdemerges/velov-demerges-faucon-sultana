#!/usr/bin/env bash
# velov.monitor planifié (crontab de la VM) : dérive et erreur réelle du modèle servi, une fois par jour.
#
#   crontab -e  ->  10 6 * * * $HOME/velov-mlops/scripts/monitor_daily.sh
#
# - le modèle jugé est celui que sert l'API à cet instant (copié depuis le conteneur, jamais models/ du dépôt) ;
# - rapport complet dans reports/<date UTC>/ (monitor.txt, monitor.json, stations.csv, drift.html) ;
# - une ligne par passage dans logs/monitor.log : OK (code 0), ALERTE (code 1, avec les alertes), RIEN À JUGER
#   (code 2) ou ERREUR ; une ALERTE ou une ERREUR part aussi dans le journal système (journalctl -t velov-monitor).
# Conduite à tenir : runbook, section 10.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs reports
LOG=logs/monitor.log
DAY=$(date -u +%F)
OUT="reports/$DAY"

exec 9>logs/monitor.lock
if ! flock -n 9; then
    echo "$(date -u +%FT%TZ) SAUTÉ : un monitor est déjà en cours" >>"$LOG"
    exit 0
fi

set -a; . ./.env; set +a
export DATABASE_URL="postgresql://velov:${POSTGRES_PASSWORD}@127.0.0.1:5432/velov"

note() {  # $1 = niveau, $2 = message ; ALERTE et ERREUR vont aussi au journal système
    echo "$(date -u +%FT%TZ) $1 $2" >>"$LOG"
    case "$1" in ALERTE|ERREUR) logger -t velov-monitor -p user.warning "$1 $2" ;; esac
}

rm -rf models/served && mkdir -p models/served
if ! docker compose cp api:/app/models/. models/served/ >/dev/null 2>&1; then
    note ERREUR "modèle servi introuvable (conteneur api arrêté ?)"
    exit 3
fi
version=$(.venv/bin/python -c "import json; print(json.load(open('models/served/metadata.json'))['model_version'])")

.venv/bin/python -m velov.monitor --db "$DATABASE_URL" --data data/velov_history.csv \
    --model-dir models/served --out "$OUT" >"logs/monitor.last.txt" 2>&1
code=$?
perf=$(grep -Eo "ratio [0-9.]+" "logs/monitor.last.txt" | head -1)
case $code in
    0) note OK "modèle $version, $perf, rapport $OUT" ;;
    1) note ALERTE "modèle $version, $perf : $(grep '^ALERTE' logs/monitor.last.txt | sed 's/^ALERTE : //' | paste -sd ';' -) ($OUT)" ;;
    2) note "RIEN-À-JUGER" "modèle $version : $(tail -1 logs/monitor.last.txt)" ;;
    *) note ERREUR "modèle $version, code $code (détail : logs/monitor.last.txt)" ;;
esac
exit $code
