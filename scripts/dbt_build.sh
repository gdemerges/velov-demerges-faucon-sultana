#!/usr/bin/env bash
# dbt build planifié (crontab de la VM) : reconstruit silver, gold et gold_features_hourly depuis bronze.
#
#   crontab -e  ->  55 * * * * $HOME/velov-mlops/scripts/dbt_build.sh
#
# - toutes les heures à :55, après le relevé du collecteur (une fois par heure, au fil de l'heure) ;
# - flock : si un build dure encore, le suivant est sauté plutôt que lancé en parallèle ;
# - une ligne par passage dans logs/dbt_build.log (OK ou ÉCHEC), le détail du dernier build dans dbt/logs/.
# Un échec ne casse rien : gold garde sa dernière version valide (tests bloquants), et la ligne ÉCHEC reste.
# Le résultat est poussé dans le Pushgateway : Prometheus alerte (DbtBuildEnEchec, DbtBuildAbsent) vers Discord.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG=logs/dbt_build.log

exec 9>logs/dbt_build.lock
if ! flock -n 9; then
    echo "$(date -u +%FT%TZ) SAUTÉ : un build est déjà en cours" >>"$LOG"
    exit 0
fi

set -a; . ./.env; set +a
export DB_HOST=127.0.0.1 DB_PORT=5432   # base publiée en local seulement (compose.monitoring.yaml)

start=$(date +%s)
if (cd dbt && ../.venv/bin/dbt build --profiles-dir . >../logs/dbt_build.last.txt 2>&1); then
    status=OK
else
    status="ÉCHEC (détail : logs/dbt_build.last.txt)"
fi
summary=$(grep -Eo "PASS=[0-9]+ WARN=[0-9]+ ERROR=[0-9]+ SKIP=[0-9]+" logs/dbt_build.last.txt | tail -1)
echo "$(date -u +%FT%TZ) $status en $(( $(date +%s) - start )) s ${summary}" >>"$LOG"
ok=0; [ "$status" = OK ] && ok=1
printf 'velov_dbt_build_success %s\nvelov_dbt_build_duration_seconds %s\nvelov_dbt_build_last_run_timestamp_seconds %s\n' \
    "$ok" "$(( $(date +%s) - start ))" "$(date +%s)" \
    | curl -fsS --max-time 10 --data-binary @- http://127.0.0.1:9091/metrics/job/velov_dbt >/dev/null 2>&1 \
    || echo "$(date -u +%FT%TZ) AVERTISSEMENT Pushgateway injoignable : Prometheus ne voit pas ce passage" >>"$LOG"
[ "$ok" = 1 ]
