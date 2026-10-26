{# Lignes dont la valeur sort de [min_value, max_value] (le nombre de lignes est comparé aux seuils warn_if / error_if). #}
{% test value_between(model, column_name, min_value, max_value) %}
select {{ column_name }} from {{ model }}
where {{ column_name }} < {{ min_value }} or {{ column_name }} > {{ max_value }}
{% endtest %}
