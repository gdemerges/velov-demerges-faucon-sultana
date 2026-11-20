"""Surveillance du modèle servi : dérive des données et erreur réelle face à la persistance (S6, EX-21).

Dans cet ordre :
1. Référence : la première semaine de la fenêtre de test du modèle servi (metadata.json), avec ses
   prédictions, recalculées par le même graphe ONNX que l'API.
2. Courant : les 3 derniers jours de la table predictions (--days), une ligne par station et par heure
   (la plus récente si une heure a été prédite plusieurs fois), pour la version servie.
3. Dérive : Evidently, un score par colonne, rapport HTML dans <out>/drift.html.
4. Erreur réelle : la prédiction faite à t est jugée avec l'observation de t + 1 h, déjà dans la table
   (S6 · 19). MAE du modèle et de la persistance sur les mêmes lignes, globale et par station.

On alerte sur l'erreur, pas sur la dérive (S6 · 23) : une dérive dit où regarder, l'erreur dit s'il faut agir.
Code de sortie 1 si une alerte de performance est levée (ratio global, ou une station où le modèle fait
pire que la persistance), 2 s'il n'y a rien à surveiller, 0 sinon.

Usage (sur la VM, base joignable en 127.0.0.1:5432) :
    python -m velov.monitor --db "$DATABASE_URL" --data data/velov_history.csv --out reports/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from velov.features import FEATURES, add_features
from velov.train import load_table

# Colonnes comparées entre référence et courant : les entrées qui varient, et la sortie du modèle.
NUMERICAL = ["temperature", "bikes_available", "occupancy_rate", "predicted_bikes"]
CATEGORICAL = ["is_raining"]
DRIFT_COLUMNS = NUMERICAL + CATEGORICAL
# Seuils calibrés par backtest pour une fenêtre de 3 jours (python -m velov.backtest, ADR-0005,
# docs/backtest/resultats.md) : quantile 99 % des passages quotidiens normaux.
# - ratio global MAE modèle / MAE persistance ;
# - par station : ratio de la pire station d'un passage, sur assez de prédictions (90 % des 72 h de la fenêtre).
# Seuils du TP6 (fenêtre 7 jours, choisis à la main) : 0,90 / 1,0 / 100, soit 16 % de passages normaux en alerte.
WINDOW_DAYS = 3
RATIO_MAX = 0.87
STATION_RATIO_MAX = 1.19
STATION_MIN_PREDICTIONS = 64

# La fenêtre se cale sur la dernière observation PASSÉE : une requête datée dans le futur (appel de test,
# horloge client fausse) déplacerait sinon toute la semaine courante sur elle.
CURRENT_SQL = """
SELECT DISTINCT ON (station_id, observed_at)
       station_id, observed_at, capacity, bikes_available, temperature, is_raining,
       predicted_bikes, model_version, created_at
FROM predictions
WHERE observed_at IS NOT NULL
  AND observed_at <= now()
  AND observed_at > (SELECT max(observed_at) FROM predictions WHERE observed_at <= now()) - make_interval(days => %s)
ORDER BY station_id, observed_at, created_at DESC
"""


# --- 1. Référence ---------------------------------------------------------------------------------


def load_metadata(model_dir: Path) -> dict:
    return json.loads((model_dir / "metadata.json").read_text(encoding="utf-8"))


def reference_window(raw: pd.DataFrame, metadata: dict, days: int) -> pd.DataFrame:
    """Les `days` premiers jours de la fenêtre de test du modèle (même découpage que velov.train)."""
    ts = pd.to_datetime(raw["timestamp"], utc=True)
    end = pd.Timestamp(metadata["data"]["end"])
    start = end - pd.Timedelta(days=metadata["metrics"]["test_days"])
    window = raw.assign(timestamp=ts)
    return window[(window["timestamp"] > start) & (window["timestamp"] <= start + pd.Timedelta(days=days))]


def predict_reference(window: pd.DataFrame, model_dir: Path) -> pd.DataFrame:
    """Prédictions du modèle servi sur la référence : même graphe ONNX et mêmes bornes que l'API."""
    from velov.artifact import onnx_feeds, onnx_session

    metadata = load_metadata(model_dir)
    session = onnx_session(model_dir / "model.onnx", 1)
    frame = add_features(window)
    preds = session.run(None, onnx_feeds(frame[FEATURES]))[0].ravel()
    return frame.assign(predicted_bikes=np.clip(preds, 0.0, frame["capacity"].to_numpy()))[
        ["station_id", "timestamp", *DRIFT_COLUMNS]
    ].assign(model_version=metadata["model_version"])


# --- 2. Courant ----------------------------------------------------------------------------------


def fetch_predictions(url: str, days: int) -> pd.DataFrame:
    """Lignes des `days` derniers jours (+ 1 h pour la vérité de la dernière heure), dédoublonnées en SQL."""
    import psycopg

    # Accepte l'URL au format SQLAlchemy de l'énoncé (postgresql+psycopg://).
    url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(url, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute(CURRENT_SQL, (days,))
        cols = [c.name for c in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def current_window(rows: pd.DataFrame, model_version: str) -> pd.DataFrame:
    """Une ligne par station et par heure pour la version servie, avec la vérité à H+1 quand elle existe.

    La vérité vient de n'importe quelle ligne de la station une heure plus tard : c'est une observation,
    peu importe quel modèle a répondu à cette requête-là.
    """
    rows = rows.assign(observed_at=pd.to_datetime(rows["observed_at"], utc=True))
    rows = rows.sort_values("created_at").drop_duplicates(["station_id", "observed_at"], keep="last")
    truth = rows[["station_id", "observed_at", "bikes_available"]].rename(
        columns={"bikes_available": "bikes_next_hour"}
    )
    truth = truth.assign(observed_at=truth["observed_at"] - pd.Timedelta(hours=1))
    current = rows[rows["model_version"] == model_version]
    current = current.merge(truth, on=["station_id", "observed_at"], how="left")
    current = current.assign(
        occupancy_rate=current["bikes_available"] / current["capacity"],
        is_raining=current["is_raining"].astype(bool),
        predicted_bikes=current["predicted_bikes"].astype(float),
        temperature=current["temperature"].astype(float),
    )
    return current.sort_values(["observed_at", "station_id"]).reset_index(drop=True)


# --- 3. Dérive -----------------------------------------------------------------------------------


def drift(reference: pd.DataFrame, current: pd.DataFrame, html: Path | None) -> dict[str, float]:
    """Score de dérive par colonne (Wasserstein normé pour les numériques, Jensen-Shannon pour is_raining)."""
    from evidently import DataDefinition, Dataset, Report
    from evidently.presets import DataDriftPreset

    if min(len(reference), len(current)) <= 1000:
        # Sous 1000 lignes, Evidently passe au test K-S : le score devient une p-value (dérive si < 0,05).
        print("Attention : 1000 lignes ou moins, les scores sont des p-values et non des distances")
    definition = DataDefinition(numerical_columns=NUMERICAL, categorical_columns=CATEGORICAL)
    # Ordre imposé par Evidently 0.7 : courant d'abord, référence ensuite (S6 · 20).
    snap = Report([DataDriftPreset()]).run(
        Dataset.from_pandas(current[DRIFT_COLUMNS], data_definition=definition),
        Dataset.from_pandas(reference[DRIFT_COLUMNS], data_definition=definition),
    )
    if html is not None:
        html.parent.mkdir(parents=True, exist_ok=True)
        snap.save_html(str(html))
    scores = {}
    for metric in snap.dict()["metrics"]:
        column = metric["config"].get("column")
        if metric["config"]["type"].endswith("ValueDrift") and column:
            scores[column] = round(float(metric["value"]), 3)
    return {c: scores[c] for c in DRIFT_COLUMNS if c in scores}


# --- 4. Erreur réelle ----------------------------------------------------------------------------


def performance(current: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """MAE du modèle et de la persistance sur les lignes qui ont leur vérité, globale et par station."""
    judged = current.dropna(subset=["bikes_next_hour"])
    if judged.empty:
        return {"n": 0, "mae_model": None, "mae_persistence": None, "ratio": None}, pd.DataFrame()
    err = judged.assign(
        err_model=(judged["predicted_bikes"] - judged["bikes_next_hour"]).abs(),
        err_persistence=(judged["bikes_available"] - judged["bikes_next_hour"]).abs(),
    )
    overall = {
        "n": int(len(err)),
        "mae_model": round(float(err["err_model"].mean()), 3),
        "mae_persistence": round(float(err["err_persistence"].mean()), 3),
    }
    overall["ratio"] = round(overall["mae_model"] / overall["mae_persistence"], 3)
    by_station = (
        err.groupby("station_id")
        .agg(n=("err_model", "size"), mae_model=("err_model", "mean"), mae_persistence=("err_persistence", "mean"))
        .assign(ratio=lambda d: d["mae_model"] / d["mae_persistence"])
        .round(3)
        .sort_values("mae_model", ascending=False)
    )
    return overall, by_station


def alerts(
    overall: dict,
    by_station: pd.DataFrame,
    ratio_max: float = RATIO_MAX,
    station_ratio_max: float = STATION_RATIO_MAX,
    station_min_predictions: int = STATION_MIN_PREDICTIONS,
) -> list[str]:
    """Alertes de performance ; les seuils sont paramétrables pour le backtest (velov.backtest)."""
    out = []
    if overall["ratio"] > ratio_max:
        out.append(f"ratio global {overall['ratio']:.2f} > {ratio_max:.2f}")
    bad = by_station[(by_station["n"] >= station_min_predictions) & (by_station["ratio"] > station_ratio_max)]
    for station, row in bad.iterrows():
        out.append(
            f"station {station} : MAE modèle {row['mae_model']:.2f} > persistance {row['mae_persistence']:.2f}"
            f" (ratio {row['ratio']:.2f})"
        )
    return out


# --- Rapport -------------------------------------------------------------------------------------


def report_text(meta: dict, reference, current, scores, overall, by_station, found, top: int = 5) -> str:
    train_ratio = meta["metrics"]["mae_model"] / meta["metrics"]["mae_baseline_persistence"]
    lines = [
        f"Modèle {meta['model_version']} · référence {len(reference)} · courant {len(current)}",
        f"Courant : {current['observed_at'].min():%Y-%m-%d %H:%M} -> {current['observed_at'].max():%Y-%m-%d %H:%M} UTC",
        "Dérive (score par colonne, seuil 0,1)",
        *(f"  {c:<17} {s:.3f}{'  dérive' if s > 0.1 else ''}" for c, s in scores.items()),
        f"Performance sur {overall['n']} prédictions",
        f"  MAE modèle       {overall['mae_model']:.3f}",
        f"  MAE persistance  {overall['mae_persistence']:.3f}  (ratio {overall['ratio']:.2f},"
        f" entraînement {train_ratio:.2f}, seuil {RATIO_MAX:.2f})",
        f"Stations les plus mal prédites (sur {by_station.shape[0]})",
        *(
            f"  station {s:>3} : MAE {r['mae_model']:.2f}, persistance {r['mae_persistence']:.2f},"
            f" ratio {r['ratio']:.2f}"
            for s, r in by_station.head(top).iterrows()
        ),
    ]
    lines += [f"ALERTE : {a}" for a in found] if found else ["Pas d'alerte de performance"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dérive et erreur réelle du modèle servi.")
    parser.add_argument("--db", required=True, help="URL PostgreSQL de la table predictions")
    parser.add_argument("--data", type=Path, required=True, help="données du modèle servi (/v1/model -> data)")
    parser.add_argument("--model-dir", type=Path, default=Path("models"), help="model.onnx + metadata.json servis")
    parser.add_argument("--out", type=Path, default=Path("reports"))
    parser.add_argument(
        "--days",
        type=int,
        default=WINDOW_DAYS,
        help="taille des fenêtres de référence et courante (seuils calibrés pour 3)",
    )
    args = parser.parse_args(argv)

    meta = load_metadata(args.model_dir)
    raw = load_table(args.data)
    from velov.train import data_fingerprint

    if data_fingerprint(raw) != meta["data"]["sha256"]:
        print(f"Attention : {args.data} n'est pas la table d'entraînement du modèle (empreinte différente)")
    reference = predict_reference(reference_window(raw, meta, args.days), args.model_dir)
    current = current_window(fetch_predictions(args.db, args.days), meta["model_version"])
    if current.empty:
        print(f"Aucune prédiction du modèle {meta['model_version']} sur les {args.days} derniers jours")
        return 2

    overall, by_station = performance(current)
    if overall["n"] == 0:
        # Sans vérité (aucune observation à t + 1 h), on ne peut rien juger : ce n'est pas « tout va bien ».
        print(f"{len(current)} prédiction(s) du modèle {meta['model_version']}, aucune avec sa vérité à H+1")
        return 2
    scores = drift(reference, current, args.out / "drift.html")
    found = alerts(overall, by_station)
    text = report_text(meta, reference, current, scores, overall, by_station, found)
    print(text)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "monitor.txt").write_text(text + "\n", encoding="utf-8")
    by_station.to_csv(args.out / "stations.csv")
    summary = {
        "model_version": meta["model_version"],
        "reference_rows": len(reference),
        "current_rows": len(current),
        "current_start": current["observed_at"].min().isoformat(),
        "current_end": current["observed_at"].max().isoformat(),
        "drift": scores,
        "performance": overall,
        "alerts": found,
    }
    (args.out / "monitor.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
