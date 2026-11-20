"""Calendrier français : jours fériés et vacances scolaires de Lyon (zone A).

Deux features calendaires, calculées depuis le timestamp de la requête : le contrat de l'API ne change pas,
et l'entraînement comme l'API passent par la même fonction (add_features), donc aucun skew possible.

- Jours fériés : calculés (dates fixes + Pâques et ses dérivés), valables pour toute année.
- Vacances scolaires : table officielle, recopiée ici pour que l'API ne dépende d'aucun service externe.
  Source : data.education.gouv.fr, jeu « fr-en-calendrier-scolaire », location = Lyon, population « Élèves »
  pour l'été ; récupérée le 2026-11-20. Couverture : SCHOOL_HOLIDAYS_COVERAGE. À compléter chaque année
  (le test test_school_holidays_cover_next_months échoue quand la couverture devient trop courte).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from functools import cache, lru_cache

import pandas as pd

logger = logging.getLogger("velov.calendrier")

# Premier jour de vacances (samedi en général) et jour de la rentrée, en dates locales : vacances = [début, rentrée).
# Le jeu officiel donne des instants UTC (minuit à Paris) ; ils sont convertis ici en dates.
# Pont de l'Ascension 2027 : début = fin dans le jeu officiel, interprété comme le seul vendredi 7 mai.
SCHOOL_HOLIDAYS_LYON: tuple[tuple[str, str, str], ...] = (
    ("2024-10-19", "2024-11-04", "Toussaint"),
    ("2024-12-21", "2025-01-06", "Noël"),
    ("2025-02-22", "2025-03-10", "Hiver"),
    ("2025-04-19", "2025-05-05", "Printemps"),
    ("2025-05-29", "2025-06-02", "Pont de l'Ascension"),
    ("2025-07-05", "2025-09-01", "Été"),
    ("2025-10-18", "2025-11-03", "Toussaint"),
    ("2025-12-20", "2026-01-05", "Noël"),
    ("2026-02-07", "2026-02-23", "Hiver"),
    ("2026-04-04", "2026-04-20", "Printemps"),
    ("2026-05-14", "2026-05-18", "Pont de l'Ascension"),
    ("2026-07-04", "2026-09-01", "Été"),
    ("2026-10-17", "2026-11-02", "Toussaint"),
    ("2026-12-19", "2027-01-04", "Noël"),
    ("2027-02-13", "2027-03-01", "Hiver"),
    ("2027-04-10", "2027-04-26", "Printemps"),
    ("2027-05-07", "2027-05-08", "Pont de l'Ascension"),
    ("2027-07-03", "2027-09-02", "Été"),
)
# Dates connues : de la rentrée 2024 à la rentrée 2027. Hors de cette plage, is_school_holiday vaut 0 (avec un
# avertissement dans les logs) : mieux qu'une API qui refuse de prédire, mais c'est un défaut à corriger.
SCHOOL_HOLIDAYS_COVERAGE = (date(2024, 9, 2), date(2027, 9, 1))


def easter_sunday(year: int) -> date:
    """Dimanche de Pâques (calendrier grégorien, algorithme de Meeus/Jones/Butcher)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return date(year, month, day + 1)


@cache
def public_holidays(year: int) -> frozenset[date]:
    """Les 11 jours fériés de métropole (hors Alsace-Moselle)."""
    easter = easter_sunday(year)
    fixed = [(1, 1), (5, 1), (5, 8), (7, 14), (8, 15), (11, 1), (11, 11), (12, 25)]
    movable = [easter + timedelta(days=n) for n in (1, 39, 50)]  # lundi de Pâques, Ascension, lundi de Pentecôte
    return frozenset([date(year, m, d) for m, d in fixed] + movable)


@lru_cache(maxsize=1)
def _school_days() -> frozenset[date]:
    days = set()
    for start, end, _ in SCHOOL_HOLIDAYS_LYON:
        d, stop = date.fromisoformat(start), date.fromisoformat(end)
        while d < stop:
            days.add(d)
            d += timedelta(days=1)
    return frozenset(days)


def is_public_holiday(local_dates: pd.Series) -> pd.Series:
    """1 si la date locale (Europe/Paris) est un jour férié."""
    lookup = {d: int(d in public_holidays(d.year)) for d in local_dates.unique()}
    return local_dates.map(lookup).astype(int)


def is_school_holiday(local_dates: pd.Series) -> pd.Series:
    """1 si la date locale tombe pendant les vacances scolaires de Lyon (zone A)."""
    lo, hi = SCHOOL_HOLIDAYS_COVERAGE
    outside = (local_dates < lo) | (local_dates >= hi)
    if outside.any():
        logger.warning(
            "%d date(s) hors du calendrier scolaire connu (%s -> %s) : is_school_holiday = 0",
            int(outside.sum()),
            lo,
            hi,
        )
    days = _school_days()
    return local_dates.map({d: int(d in days) for d in local_dates.unique()}).astype(int)
