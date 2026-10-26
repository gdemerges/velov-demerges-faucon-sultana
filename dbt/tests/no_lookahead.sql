{# EX-11 : toute feature de gold vaut ce que silver contenait à l'instant t ; seule la cible vient du futur,
   et uniquement de la ligne située exactement une heure plus tard. Retourne les lignes en défaut. #}
select g.station_id, g."timestamp"
from {{ ref('gold_training_hourly') }} g
join {{ ref('silver_station_status') }} s
  on s.station_id = g.station_id and s.observed_at = g."timestamp"
left join {{ ref('silver_station_status') }} nxt
  on nxt.station_id = g.station_id and nxt.observed_at = g."timestamp" + interval '1 hour'
where g.capacity <> s.capacity
   or g.bikes_available <> s.bikes_available
   or g.temperature <> s.temperature
   or g.is_raining <> s.is_raining
   or g.bikes_next_hour is distinct from nxt.bikes_available
