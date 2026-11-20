{# EX-11 pour gold_features_hourly : l'historique vient bien du passé (relevés à t - 1 h et t - 24 h),
   et la prévision utilisée a été publiée à t, pour t + 1 h. Retourne les lignes en défaut. #}
select g.station_id, g."timestamp"
from {{ ref('gold_features_hourly') }} g
left join {{ ref('silver_station_status') }} h1
  on h1.station_id = g.station_id and h1.observed_at = g."timestamp" - interval '1 hour'
left join {{ ref('silver_station_status') }} h24
  on h24.station_id = g.station_id and h24.observed_at = g."timestamp" - interval '24 hours'
left join {{ source('bronze', 'weather_forecast') }} f
  on f.issued_at = g."timestamp" and f.target_at = g."timestamp" + interval '1 hour'
where g.bikes_lag_1h is distinct from h1.bikes_available
   or g.bikes_lag_24h is distinct from h24.bikes_available
   or g.forecast_precipitation_mm_next_hour is distinct from f.precipitation_mm
