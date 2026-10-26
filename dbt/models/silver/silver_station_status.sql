{# Silver : relevés typés, dédoublonnés et physiquement plausibles, une ligne par station et par heure.
   Les lignes rejetées (capacité absente ou nulle, vélos hors [0, capacité]) restent dans bronze. #}
with ranked as (
    select
        station_id,
        observed_at,
        capacity,
        bikes_available,
        temperature,
        is_raining,
        row_number() over (partition by station_id, observed_at order by ingested_at) as rn
    from {{ source('bronze', 'station_status') }}
)

select
    station_id::integer          as station_id,
    observed_at::timestamptz     as observed_at,
    capacity::integer            as capacity,
    bikes_available::integer     as bikes_available,
    temperature::double precision as temperature,
    coalesce(is_raining, false)  as is_raining
from ranked
where rn = 1
  and capacity > 0
  and bikes_available between 0 and capacity
  and temperature is not null
