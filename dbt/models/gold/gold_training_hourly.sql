{# Gold : table d'entraînement, une ligne par station et par heure, au schéma du contrat.

   Les colonnes de features sont celles de l'instant t, jamais postérieures (EX-11).
   La cible bikes_next_hour est le seul champ du futur : elle n'existe que si la ligne
   suivante est exactement une heure plus tard (un trou dans la collecte ne fabrique pas de cible). #}
with labelled as (
    select
        station_id,
        observed_at as "timestamp",
        capacity,
        bikes_available,
        temperature,
        is_raining,
        case
            when lead(observed_at) over w = observed_at + interval '1 hour'
            then lead(bikes_available) over w
        end as bikes_next_hour
    from {{ ref('silver_station_status') }}
    window w as (partition by station_id order by observed_at)
)

select * from labelled where bikes_next_hour is not null
