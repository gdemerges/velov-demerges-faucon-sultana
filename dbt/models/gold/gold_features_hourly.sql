{# Features d'enrichissement, une ligne par station et par heure de gold_training_hourly (même cible).

   Pas encore utilisées par le modèle : elles s'accumulent sur les données réelles pour mesurer leur apport
   (README, « Données d'enrichissement »). Toutes sont connues à l'instant t (EX-11, test features_no_lookahead) :
   - historique de la station : relevés exactement 1 h, 24 h et 168 h avant t (absent s'il manque) ;
   - voisines (< 500 m) : occupation moyenne à t ;
   - prévision météo publiée à t pour t + 1 h ;
   - contexte fixe de la station ; événement le jour local de t. #}
with base as (
    select * from {{ ref('gold_training_hourly') }}
),

neighbors_now as (
    select
        n.station_id,
        s.observed_at,
        avg(s.bikes_available::double precision / s.capacity) as neighbors_occupancy,
        count(*)                                              as neighbors_observed
    from {{ ref('silver_station_neighbors') }} n
    join {{ ref('silver_station_status') }} s on s.station_id = n.neighbor_id
    group by n.station_id, s.observed_at
)

select
    b.station_id,
    b."timestamp",
    b.capacity,
    b.bikes_available,
    b.temperature,
    b.is_raining,
    h1.bikes_available                    as bikes_lag_1h,
    h24.bikes_available                   as bikes_lag_24h,
    h168.bikes_available                  as bikes_lag_168h,
    nn.neighbors_occupancy,
    nn.neighbors_observed,
    f.precipitation_mm                    as forecast_precipitation_mm_next_hour,
    f.precipitation_probability           as forecast_precipitation_probability_next_hour,
    f.temperature                         as forecast_temperature_next_hour,
    c.elevation_m,
    c.elevation_vs_neighbors_m,
    c.n_neighbors,
    c.dist_gare_m,
    c.dist_universite_m,
    c.pole,
    e.event is not null                   as is_event_day,
    e.event,
    b.bikes_next_hour
from base b
left join {{ ref('silver_station_status') }} h1
  on h1.station_id = b.station_id and h1.observed_at = b."timestamp" - interval '1 hour'
left join {{ ref('silver_station_status') }} h24
  on h24.station_id = b.station_id and h24.observed_at = b."timestamp" - interval '24 hours'
left join {{ ref('silver_station_status') }} h168
  on h168.station_id = b.station_id and h168.observed_at = b."timestamp" - interval '168 hours'
left join neighbors_now nn
  on nn.station_id = b.station_id and nn.observed_at = b."timestamp"
left join {{ source('bronze', 'weather_forecast') }} f
  on f.issued_at = b."timestamp" and f.target_at = b."timestamp" + interval '1 hour'
left join {{ ref('silver_station_context') }} c
  on c.station_id = b.station_id
left join (
    select event_date, string_agg(event, ' ; ' order by event) as event
    from {{ ref('events_lyon') }} group by event_date
) e
  on e.event_date = (b."timestamp" at time zone 'Europe/Paris')::date
