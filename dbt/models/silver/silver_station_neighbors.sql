{# Paires de stations distantes de moins de 500 m (environ 6 min à pied) : les « voisines ». #}
select
    a.station_id,
    b.station_id                                                   as neighbor_id,
    {{ haversine_m('a.lat', 'a.lng', 'b.lat', 'b.lng') }}          as distance_m
from {{ ref('silver_station_info') }} a
join {{ ref('silver_station_info') }} b
  on a.station_id <> b.station_id
 and abs(a.lat - b.lat) < 0.01 and abs(a.lng - b.lng) < 0.01   -- préfiltre (~1 km) avant le calcul exact
where {{ haversine_m('a.lat', 'a.lng', 'b.lat', 'b.lng') }} < 500
