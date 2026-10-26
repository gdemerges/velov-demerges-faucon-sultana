"""Contrat gold (Pandera) : la table valide passe, chaque violation est refusée avant l'entraînement (EX-12)."""

from __future__ import annotations

import pandas as pd
import pandera.errors
import pytest

from velov import contract


@pytest.fixture
def gold() -> pd.DataFrame:
    ts = pd.date_range("2026-06-01", periods=3, freq="h", tz="UTC").as_unit("us")
    return pd.DataFrame(
        {
            "station_id": [1, 1, 1],
            "timestamp": ts,
            "capacity": [20, 20, 20],
            "bikes_available": [5, 6, 7],
            "temperature": [18.0, 18.5, 19.0],
            "is_raining": [False, False, True],
            "bikes_next_hour": [6, 7, 8],
        }
    )


def test_valid_gold_passes(gold):
    assert len(contract.validate(gold)) == 3


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("capacity", 0),  # capacité hors bornes
        ("capacity", 500),
        ("bikes_available", 99),  # plus de vélos que de places
        ("bikes_next_hour", -1),
        ("temperature", 120.0),
    ],
)
def test_out_of_range_values_are_rejected(gold, column, value):
    gold.loc[0, column] = value
    with pytest.raises(pandera.errors.SchemaErrors):
        contract.validate(gold)


def test_missing_and_extra_columns_are_rejected(gold):
    with pytest.raises(pandera.errors.SchemaErrors):
        contract.validate(gold.drop(columns=["temperature"]))
    with pytest.raises(pandera.errors.SchemaErrors):
        contract.validate(gold.assign(extra=1))


def test_null_and_duplicate_rows_are_rejected(gold):
    with pytest.raises(pandera.errors.SchemaErrors):
        contract.validate(gold.assign(temperature=[18.0, None, 19.0]))
    with pytest.raises(pandera.errors.SchemaErrors):
        contract.validate(pd.concat([gold, gold.iloc[[0]]], ignore_index=True))
