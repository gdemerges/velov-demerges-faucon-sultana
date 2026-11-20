"""Rejoue une semaine d'observations contre l'API, heure par heure (S6).

But : remplir la table predictions comme le ferait l'application, pour que le
monitoring ait quelque chose à surveiller. Chaque heure, toutes les stations sont
envoyées en un appel batch. La vérité terrain d'une prédiction faite à t est la
disponibilité observée à t + 1 h, qui arrive avec l'appel de l'heure suivante.

Les dates sont décalées d'un nombre entier de semaines, en heure locale, pour que
la semaine rejouée se termine aujourd'hui : l'heure et le jour de la semaine
restent ceux des données d'origine (pas de décalage dû au changement d'heure).

Scénarios (injectés par l'intervenant, ou par vous pour tester le monitoring) :
    normal        les données telles quelles
    froid         -15 °C sur toute la semaine
    demenagement  à partir du 3e jour, les stations de bureaux (2, 5, 8...) se
                  comportent comme des stations résidentielles

Usage :
    python -m velov.replay --url https://192.168.1.2xx --cacert certs/velov.crt
    python -m velov.replay --url http://127.0.0.1:8000 --scenario froid
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from velov.features import LOCAL_TZ, RAW_COLUMNS
from velov.train import load_table

SCENARIOS = ("normal", "froid", "demenagement")


def last_days(df: pd.DataFrame, days: int) -> pd.DataFrame:
    ts = pd.to_datetime(df["timestamp"], utc=True)
    out = df.assign(timestamp=ts)
    return out[out["timestamp"] > ts.max() - pd.Timedelta(days=days)].copy()


def apply_scenario(df: pd.DataFrame, scenario: str) -> pd.DataFrame:
    out = df.copy()
    if scenario == "froid":
        out["temperature"] = (out["temperature"] - 15).round(1)
    elif scenario == "demenagement":
        start = out["timestamp"].min() + pd.Timedelta(days=2)
        # Station 1 : résidentielle. Les stations 2, 5, 8... (profil bureaux) prennent son taux d'occupation.
        ref = out[out["station_id"] == 1].set_index("timestamp")
        ref_occ = ref["bikes_available"] / ref["capacity"]
        moved = (out["station_id"] % 3 == 2) & (out["timestamp"] >= start)
        occ = out.loc[moved, "timestamp"].map(ref_occ)
        out.loc[moved, "bikes_available"] = np.clip(
            np.rint(occ * out.loc[moved, "capacity"]), 0, out.loc[moved, "capacity"]
        ).astype(int)
    elif scenario != "normal":
        raise ValueError(f"Scénario inconnu : {scenario} (choix : {', '.join(SCENARIOS)})")
    return out


def shift_to_this_week(df: pd.DataFrame) -> pd.DataFrame:
    """Décale d'un nombre entier de semaines, en heure locale, pour finir avant maintenant."""
    now = pd.Timestamp.now(tz="UTC")
    weeks = int((now - df["timestamp"].max()) // pd.Timedelta(weeks=1))
    local = df["timestamp"].dt.tz_convert(LOCAL_TZ).dt.tz_localize(None) + pd.Timedelta(weeks=weeks)
    shifted = local.dt.tz_localize(LOCAL_TZ, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
    return df.assign(timestamp=shifted).dropna(subset=["timestamp"])


def replay(df: pd.DataFrame, url: str, verify: str | bool, pause: float) -> tuple[int, int]:
    sent, errors = 0, 0
    with httpx.Client(base_url=url, verify=verify, timeout=10) as client:
        for ts, hour in df.groupby("timestamp", sort=True):
            items = hour[RAW_COLUMNS].assign(timestamp=ts.isoformat()).to_dict(orient="records")
            for item in items:
                item["is_raining"] = bool(item["is_raining"])
            r = client.post("/v1/predict/batch", json={"items": items})
            sent += len(items)
            if r.status_code != 200:
                errors += 1
                print(f"{ts} : HTTP {r.status_code} {r.text[:120]}")
            if pause:
                time.sleep(pause)
    return sent, errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Rejoue une semaine d'observations contre l'API.")
    parser.add_argument("--url", required=True, help="adresse de l'API, ex. https://192.168.1.237")
    parser.add_argument("--cacert", type=Path, help="certificat à faire confiance (TLS auto-signé)")
    parser.add_argument("--data", type=Path, default=Path("data/velov_history.csv"))
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--scenario", choices=SCENARIOS, default="normal")
    parser.add_argument("--pause", type=float, default=0.0, help="secondes entre deux heures rejouées")
    args = parser.parse_args()

    df = shift_to_this_week(apply_scenario(last_days(load_table(args.data), args.days), args.scenario))
    verify: str | bool = str(args.cacert) if args.cacert else True
    start = time.perf_counter()
    sent, errors = replay(df, args.url, verify, args.pause)
    print(
        f"{sent} observations rejouées ({df['timestamp'].nunique()} heures, scénario {args.scenario}) "
        f"en {time.perf_counter() - start:.1f} s, {errors} appel(s) en erreur"
    )


if __name__ == "__main__":
    main()
