{# Référentiel des stations : dernière capacité connue. #}
select distinct on (station_id)
    station_id,
    capacity
from {{ ref('silver_station_status') }}
order by station_id, observed_at desc
