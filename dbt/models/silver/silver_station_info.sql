{# Référentiel typé des stations ; positions hors de la métropole écartées (coordonnée absente ou erronée). #}
select
    station_id::integer             as station_id,
    name,
    lat::double precision           as lat,
    lng::double precision           as lng,
    commune,
    nullif(pole, '')                as pole,
    elevation_m::double precision   as elevation_m
from {{ source('bronze', 'station_info') }}
where lat between 45.5 and 46.0
  and lng between 4.6 and 5.2
