{# Unicité d'une combinaison de colonnes, sans dépendre du paquet dbt_utils (clone propre sans `dbt deps`). #}
{% test dbt_utils_free_unique_combination(model, columns) %}
select {{ columns | join(', ') }}, count(*) as n
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}
