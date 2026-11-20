"""Jours fériés et vacances scolaires (velov.calendrier), et leur passage dans add_features."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from velov.calendrier import (
    SCHOOL_HOLIDAYS_COVERAGE,
    SCHOOL_HOLIDAYS_LYON,
    easter_sunday,
    is_school_holiday,
    public_holidays,
)
from velov.data import simulate
from velov.features import add_features


@pytest.mark.parametrize(
    ("year", "expected"), [(2024, date(2024, 3, 31)), (2025, date(2025, 4, 20)), (2026, date(2026, 4, 5))]
)
def test_easter(year, expected):
    assert easter_sunday(year) == expected


def test_public_holidays_2026():
    days = public_holidays(2026)
    assert len(days) == 11
    # Lundi de Pâques, Ascension, lundi de Pentecôte
    assert {date(2026, 4, 6), date(2026, 5, 14), date(2026, 5, 25)} <= days
    assert date(2026, 7, 14) in days and date(2026, 7, 13) not in days


def test_school_holidays_boundaries():
    dates = pd.Series([date(2026, 10, 16), date(2026, 10, 17), date(2026, 11, 1), date(2026, 11, 2)])
    # Toussaint 2026 : du samedi 17 octobre au dimanche 1er novembre, rentrée le lundi 2
    assert is_school_holiday(dates).tolist() == [0, 1, 1, 0]


def test_school_holidays_table_is_ordered_and_disjoint():
    periods = [(date.fromisoformat(a), date.fromisoformat(b)) for a, b, _ in SCHOOL_HOLIDAYS_LYON]
    assert all(start < end for start, end in periods)
    assert all(prev_end <= start for (_, prev_end), (start, _) in zip(periods, periods[1:], strict=False))


def test_school_holidays_cover_next_months():
    """Échoue quand la table officielle n'est plus à jour : à compléter depuis data.education.gouv.fr."""
    assert SCHOOL_HOLIDAYS_COVERAGE[1] >= date.today() + pd.Timedelta(days=180)


def test_outside_coverage_is_zero_and_logged(caplog):
    assert is_school_holiday(pd.Series([date(2030, 8, 1)])).tolist() == [0]
    assert "hors du calendrier scolaire" in caplog.text


def test_add_features_uses_local_date():
    # 23 h 30 UTC le 13 juillet = 1 h 30 le 14 juillet à Lyon : jour férié, et vacances d'été.
    df = simulate(days=2, n_stations=1).head(1).assign(timestamp=pd.Timestamp("2026-07-13T23:30Z"))
    out = add_features(df)
    assert out.loc[0, "is_public_holiday"] == 1
    assert out.loc[0, "is_school_holiday"] == 1


def test_api_prediction_unchanged_contract(client, valid_payload):
    """Le contrat d'entrée ne change pas : les deux features viennent du timestamp."""
    r = client.post("/v1/predict", json={**valid_payload, "timestamp": "2026-12-25T08:00:00+01:00"})
    assert r.status_code == 200
