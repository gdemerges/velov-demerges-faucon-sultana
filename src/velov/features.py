"""Construction des features, partagée entre l'entraînement ET l'API.

Règle d'or : une seule implémentation des features, importée par les deux côtés.
Réécrire ce calcul dans l'API (copier-coller, autre langage, autre équipe) est la
cause n°1 du training-serving skew : le modèle reçoit en production des entrées
calculées différemment de celles vues à l'entraînement, sans aucune erreur visible.
"""

from __future__ import annotations

import pandas as pd

from velov.calendrier import is_public_holiday, is_school_holiday

TARGET = "bikes_next_hour"

# Les instants circulent en UTC, mais les usages suivent l'heure locale de Lyon :
# les features calendaires (heure, jour) sont calculées dans ce fuseau.
LOCAL_TZ = "Europe/Paris"

# Colonnes fournies par l'appelant (contrat d'entrée de l'API).
RAW_COLUMNS = [
    "station_id",
    "timestamp",
    "capacity",
    "bikes_available",
    "temperature",
    "is_raining",
]

# Colonnes réellement données au modèle, dans cet ordre.
CATEGORICAL_FEATURES = ["station_id"]
NUMERIC_FEATURES = [
    "hour",
    "day_of_week",
    "is_weekend",
    "is_public_holiday",  # calculés depuis timestamp (velov.calendrier) : le contrat d'entrée ne change pas
    "is_school_holiday",
    "capacity",
    "bikes_available",
    "occupancy_rate",
    "temperature",
    "is_raining",
]
FEATURES = CATEGORICAL_FEATURES + NUMERIC_FEATURES


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Ajoute les features calendaires et dérivées. Ne modifie pas l'entrée.

    Raises:
        ValueError: si une colonne brute manque, ou si les timestamps n'ont pas de fuseau.
    """
    missing = set(RAW_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"Colonnes manquantes : {sorted(missing)}")
    if not isinstance(df["timestamp"].dtype, pd.DatetimeTZDtype):
        # Un timestamp sans fuseau est ambigu : 8 h à Lyon ou 8 h UTC ? On refuse plutôt que deviner.
        raise ValueError("timestamp doit être un datetime avec fuseau horaire (UTC conseillé)")

    out = df.copy()
    ts = out["timestamp"].dt.tz_convert(LOCAL_TZ)  # heure locale pour les features calendaires
    out["hour"] = ts.dt.hour
    out["day_of_week"] = ts.dt.dayofweek
    out["is_weekend"] = (ts.dt.dayofweek >= 5).astype(int)
    local_dates = ts.dt.date
    out["is_public_holiday"] = is_public_holiday(local_dates)
    out["is_school_holiday"] = is_school_holiday(local_dates)
    out["occupancy_rate"] = out["bikes_available"] / out["capacity"]
    out["is_raining"] = out["is_raining"].astype(int)
    out["station_id"] = out["station_id"].astype(str)  # catégorie, pas un nombre
    return out


def make_training_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Features + cible (vélos disponibles à H+1, par station).

    Le décalage se fait PAR station (groupby) : sinon la dernière heure d'une station
    recevrait la première heure de la station suivante comme cible (fuite silencieuse).
    """
    out = add_features(df).sort_values(["station_id", "timestamp"])
    out[TARGET] = out.groupby("station_id")["bikes_available"].shift(-1)
    return out.dropna(subset=[TARGET]).reset_index(drop=True)
