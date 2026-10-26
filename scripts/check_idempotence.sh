#!/usr/bin/env bash
# EX-10 : rejouer le pipeline sur la même période ne change ni bronze ni gold.
# Empreinte (md5 des lignes triées) de bronze et de gold avant et après un second passage complet.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

fingerprint() {
  docker compose exec -T db psql -U velov -d velov -At -c "
    select 'bronze', count(*), md5(string_agg(t::text, '|' order by station_id, observed_at))
      from (select station_id, observed_at, capacity, bikes_available, temperature, is_raining from bronze.station_status) t
    union all
    select 'gold', count(*), md5(string_agg(g::text, '|' order by station_id, \"timestamp\"))
      from gold.gold_training_hourly g"
}

./scripts/run_pipeline.sh >/dev/null
first=$(fingerprint)
./scripts/run_pipeline.sh >/dev/null
second=$(fingerprint)

echo "$first"
if [ "$first" = "$second" ]; then echo "OK : second passage identique (EX-10)"; else echo "ÉCHEC : le rejeu a modifié les tables"; echo "$second"; exit 1; fi
