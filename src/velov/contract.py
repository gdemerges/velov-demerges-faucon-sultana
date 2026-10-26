"""Contrat de la table gold `gold.gold_training_hourly`, vérifié avant tout entraînement (EX-12).

C'est la même définition que `dbt/models/gold/gold.yml` (types, non-nullité, bornes), exprimée en
Pandera : dbt garde la table de se publier hors contrat, Pandera garde le modèle de s'entraîner
sur autre chose que le contrat (table modifiée à la main, mauvaise base, ancienne version).
"""

from __future__ import annotations

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series

from velov.features import TARGET

GOLD_TABLE = "gold.gold_training_hourly"
MAX_CAPACITY = 80


class GoldContract(pa.DataFrameModel):
    station_id: Series[int] = pa.Field(ge=0)
    timestamp: Series[pd.DatetimeTZDtype] = pa.Field(dtype_kwargs={"unit": "us", "tz": "UTC"})
    capacity: Series[int] = pa.Field(ge=1, le=MAX_CAPACITY)
    bikes_available: Series[int] = pa.Field(ge=0)
    temperature: Series[float] = pa.Field(ge=-30, le=50)
    is_raining: Series[bool]
    bikes_next_hour: Series[int] = pa.Field(ge=0, alias=TARGET)

    class Config:
        strict = True  # une colonne en plus ou en moins viole le contrat
        unique = ["station_id", "timestamp"]

    @pa.dataframe_check
    def bikes_within_capacity(cls, df: pd.DataFrame) -> Series[bool]:
        return (df["bikes_available"] <= df["capacity"]) & (df[TARGET] <= df["capacity"])


def validate(df: pd.DataFrame) -> pd.DataFrame:
    """Retourne le DataFrame si le contrat est respecté, sinon lève pandera.errors.SchemaErrors."""
    return GoldContract.validate(df, lazy=True)  # lazy : toutes les violations d'un coup


def read_gold(url: str) -> pd.DataFrame:
    """Lit la table gold complète, triée de façon déterministe."""
    import psycopg

    with psycopg.connect(url, connect_timeout=5) as conn:
        cur = conn.execute(f'SELECT * FROM {GOLD_TABLE} ORDER BY station_id, "timestamp"')  # noqa: S608
        columns = [c.name for c in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=columns)
