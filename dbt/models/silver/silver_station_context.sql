{# Contexte fixe de chaque station : altitude, écart d'altitude avec ses voisines (pente locale),
   distance à la gare et à l'établissement d'enseignement supérieur les plus proches. #}
with poi as (
    select
        s.station_id,
        min(case when p.kind = 'gare' then {{ haversine_m('s.lat', 's.lng', 'p.lat', 'p.lon') }} end)       as dist_gare_m,
        min(case when p.kind = 'universite' then {{ haversine_m('s.lat', 's.lng', 'p.lat', 'p.lon') }} end) as dist_universite_m
    from {{ ref('silver_station_info') }} s
    cross join {{ ref('poi_lyon') }} p
    group by s.station_id
),

neighbors as (
    select
        n.station_id,
        count(*)               as n_neighbors,
        avg(i.elevation_m)     as neighbors_elevation_m
    from {{ ref('silver_station_neighbors') }} n
    join {{ ref('silver_station_info') }} i on i.station_id = n.neighbor_id
    group by n.station_id
)

select
    s.station_id,
    s.commune,
    s.pole,
    s.elevation_m,
    s.elevation_m - nb.neighbors_elevation_m   as elevation_vs_neighbors_m,   -- > 0 : la station domine ses voisines
    coalesce(nb.n_neighbors, 0)                as n_neighbors,
    poi.dist_gare_m,
    poi.dist_universite_m
from {{ ref('silver_station_info') }} s
left join neighbors nb on nb.station_id = s.station_id
left join poi on poi.station_id = s.station_id
