{# Distance en mètres entre deux points (latitude, longitude en degrés), formule de haversine. #}
{% macro haversine_m(lat1, lon1, lat2, lon2) -%}
    (2 * 6371000 * asin(sqrt(
        power(sin(radians(({{ lat2 }}) - ({{ lat1 }})) / 2), 2)
        + cos(radians({{ lat1 }})) * cos(radians({{ lat2 }})) * power(sin(radians(({{ lon2 }}) - ({{ lon1 }})) / 2), 2)
    )))
{%- endmacro %}
