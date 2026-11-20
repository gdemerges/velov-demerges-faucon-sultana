"""Backtest et calibration du monitoring du modèle (extension « Monitoring ML planifié », docs/extension.md).

Question : le monitor quotidien prévient-il d'une vraie dégradation en moins de 48 h, sans alerter sur une
semaine ordinaire ? On rejoue l'historique comme si velov.monitor avait tourné chaque jour.

1. Exécutions normales : un passage par jour à 6 h UTC, chacun juge la fenêtre précédente (prédictions dont la
   vérité à H+1 est connue au moment du passage), avec les fonctions de velov.monitor (performance, alerts).
   La longueur de fenêtre est elle-même testée (2, 3 et 7 jours) : c'est elle qui fixe le délai de détection.
   Le modèle est réentraîné chaque semaine, uniquement sur ce qui précède (jamais sur les données jugées).
2. Calibration, sur les seules exécutions normales :
   - minimum de prédictions par station : le plus petit n pour lequel l'intervalle de confiance à 95 % du ratio
     d'une station (bootstrap par blocs de 6 h, les erreurs horaires étant corrélées) a une demi-largeur <= 0,1 ;
   - seuil global : quantile 99 % des ratios globaux normaux ;
   - seuil par station : quantile 99 % du ratio de la PIRE station de chaque passage (20 stations jugées à
     chaque passage : calibrer station par station donnerait ~1 - 0,99^20 = 18 % de passages en alerte) ;
   arrondis au centième supérieur.
3. Scénarios injectés à une date de début D (chaque jour possible) : le monitor tourne à D + 6 h, D + 30 h...
   Délai = premier passage en alerte - D. Comparaison seuils du TP6 / seuils calibrés : fausses alertes,
   détection en 48 h et en 7 jours, délai médian.

Usage :
    python -m velov.backtest --data data/velov_history.csv --out docs/backtest
    python -m velov.backtest ... --mlflow     # un run MLflow par jeu de seuils (MLFLOW_TRACKING_URI)
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from velov import monitor
from velov.features import FEATURES, TARGET, make_training_frame
from velov.replay import apply_scenario
from velov.train import DEFAULT_PARAMS, build_pipeline, load_table

DAY = pd.Timedelta(days=1)
HOUR = pd.Timedelta(hours=1)
WINDOW = pd.Timedelta(days=7)  # fenêtre de velov.monitor (--days 7) ; le backtest en teste d'autres
WINDOWS_DAYS = (2, 3, 7)
RUN_HOUR = pd.Timedelta(hours=6)  # la crontab lance le monitor à 6 h 10 UTC
MIN_TRAIN = pd.Timedelta(days=28)  # au moins 4 semaines d'historique avant le premier modèle
RETRAIN_EVERY = pd.Timedelta(days=7)
BLOCK_HOURS = 6
TARGET_HALF_WIDTH = 0.1
QUANTILE = 0.99
SCENARIOS = ("froid", "demenagement")  # les scénarios conçus (capteur bloqué, chantier) s'ajouteront ici


@dataclass(frozen=True)
class Thresholds:
    name: str
    ratio_max: float
    station_ratio_max: float
    station_min_predictions: int


# Seuils choisis à la main au TP6 (fenêtre 7 jours), figés ici comme référence de comparaison.
FIXED = Thresholds("seuils TP6", 0.90, 1.00, 100)


# --- Modèles et prédictions ----------------------------------------------------------------------


def train_models(frame: pd.DataFrame, cutoffs: list[pd.Timestamp], seed: int = 42) -> dict:
    """Un modèle par date de coupure, entraîné sur les lignes dont la cible (t + 1 h) précède la coupure."""
    models = {}
    for cutoff in cutoffs:
        train = frame[frame["timestamp"] + HOUR < cutoff]
        models[cutoff] = build_pipeline(seed, **DEFAULT_PARAMS).fit(train[FEATURES], train[TARGET])
    return models


def model_for(models: dict, at: pd.Timestamp):
    """Le dernier modèle dont la coupure précède `at` : celui qui serait en service à cette date."""
    cutoff = max(c for c in models if c <= at)
    return cutoff, models[cutoff]


def predict(model, rows: pd.DataFrame) -> pd.DataFrame:
    """Lignes au format de velov.monitor.current_window (prédiction bornée comme dans l'API)."""
    preds = np.clip(model.predict(rows[FEATURES]), 0, rows["capacity"].to_numpy())
    return pd.DataFrame(
        {
            "station_id": rows["station_id"].astype(int).to_numpy(),
            "observed_at": rows["timestamp"].to_numpy(),
            "bikes_available": rows["bikes_available"].to_numpy(),
            "bikes_next_hour": rows[TARGET].to_numpy(),
            "predicted_bikes": preds,
        }
    )


def judged_at(predictions: pd.DataFrame, run_at: pd.Timestamp, window: pd.Timedelta = WINDOW) -> pd.DataFrame:
    """Ce que voit un passage à `run_at` : la fenêtre de prédictions dont la vérité (t + 1 h) est connue."""
    t = pd.to_datetime(predictions["observed_at"], utc=True)
    return predictions[(t > run_at - window) & (t + HOUR <= run_at)]


# --- 1. Exécutions normales ----------------------------------------------------------------------


def run_times(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    first = (start + MIN_TRAIN + WINDOW).ceil("D") + RUN_HOUR
    return list(pd.date_range(first, end, freq="D"))


def normal_runs(frame: pd.DataFrame, models: dict, runs: list[pd.Timestamp], window: pd.Timedelta = WINDOW):
    """Ratio global et par station de chaque passage quotidien sur l'historique non modifié."""
    rows, stations, errors = [], [], {}
    for run_at in runs:
        cutoff, model = model_for(models, run_at - WINDOW)  # même modèle quelle que soit la fenêtre
        judged = frame[(frame["timestamp"] > run_at - window) & (frame["timestamp"] + HOUR <= run_at)]
        current = predict(model, judged)
        overall, by_station = monitor.performance(current)
        rows.append({"run_at": run_at, "model_cutoff": cutoff, **overall})
        stations.append(by_station.reset_index().assign(run_at=run_at))
        errors[run_at] = current.sort_values("observed_at")
    return pd.DataFrame(rows), pd.concat(stations, ignore_index=True), errors


# --- 2. Calibration ------------------------------------------------------------------------------


def block_bootstrap_half_width(
    err_model: np.ndarray, err_persistence: np.ndarray, n: int, rng: np.random.Generator, draws: int = 300
) -> float:
    """Demi-largeur de l'IC à 95 % du ratio MAE modèle / persistance d'une station jugée sur n prédictions."""
    length = len(err_model)
    n_blocks = math.ceil(n / BLOCK_HOURS)
    starts = rng.integers(0, length - BLOCK_HOURS + 1, size=(draws, n_blocks))
    idx = (starts[..., None] + np.arange(BLOCK_HOURS)).reshape(draws, -1)[:, :n]
    ratios = err_model[idx].sum(axis=1) / np.maximum(err_persistence[idx].sum(axis=1), 1e-9)
    lo, hi = np.quantile(ratios, [0.025, 0.975])
    return float((hi - lo) / 2)


def min_predictions_curve(errors: dict, sizes: list[int], seed: int = 42) -> pd.DataFrame:
    """Demi-largeur médiane de l'IC par taille n, sur des passages espacés d'une semaine (indépendants)."""
    rng = np.random.default_rng(seed)
    runs = sorted(errors)[::7]
    out = []
    for n in sizes:
        widths = []
        for run_at in runs:
            current = errors[run_at]
            for _, st in current.groupby("station_id"):
                em = (st["predicted_bikes"] - st["bikes_next_hour"]).abs().to_numpy()
                ep = (st["bikes_available"] - st["bikes_next_hour"]).abs().to_numpy()
                if len(em) >= max(n, BLOCK_HOURS) and ep.sum() > 0:
                    widths.append(block_bootstrap_half_width(em, ep, n, rng))
        out.append({"n": n, "half_width_median": float(np.median(widths)), "station_runs": len(widths)})
    return pd.DataFrame(out)


def ceil2(x: float) -> float:
    return math.ceil(round(x * 100, 6)) / 100


def calibrate(
    runs: pd.DataFrame, stations: pd.DataFrame, curve: pd.DataFrame, window: pd.Timedelta, name: str
) -> Thresholds:
    """Seuils pris sur les passages normaux. Le minimum par station vise un IC de ±0,1, plafonné à 90 % des
    heures de la fenêtre : une fenêtre courte ne l'atteint pas, c'est alors le seuil qui absorbe le bruit."""
    ok = curve[curve["half_width_median"] <= TARGET_HALF_WIDTH]
    min_n = int(ok["n"].min()) if not ok.empty else int(curve["n"].max())
    min_n = min(min_n, int(0.9 * window / HOUR))
    worst = stations[stations["n"] >= min_n].groupby("run_at")["ratio"].max()
    return Thresholds(
        name,
        ceil2(float(runs["ratio"].quantile(QUANTILE))),
        ceil2(float(worst.quantile(QUANTILE))),
        min_n,
    )


def false_alarms(runs: pd.DataFrame, stations: pd.DataFrame, th: Thresholds) -> dict:
    """Part des passages normaux qui auraient alerté (global, par station, au moins une des deux)."""
    by_run = stations[stations["n"] >= th.station_min_predictions].groupby("run_at")["ratio"].max()
    station_alert = runs["run_at"].map(by_run).fillna(0) > th.station_ratio_max
    global_alert = runs["ratio"] > th.ratio_max
    return {
        "false_alarm_global": round(float(global_alert.mean()), 3),
        "false_alarm_station": round(float(station_alert.mean()), 3),
        "false_alarm_any": round(float((global_alert | station_alert).mean()), 3),
        "normal_runs": int(len(runs)),
    }


# --- 3. Scénarios --------------------------------------------------------------------------------


def scenario_delays(
    raw: pd.DataFrame,
    models: dict,
    starts: list[pd.Timestamp],
    scenario: str,
    all_th: list[tuple[Thresholds, pd.Timedelta]],
) -> pd.DataFrame:
    """Pour chaque début D : premier passage en alerte (D + 6 h, D + 30 h... jusqu'à D + 7 jours), par seuils."""
    out = []
    for start in starts:
        frame = make_training_frame(apply_scenario(raw, scenario, start=start))
        _, model = model_for(models, start)  # le modèle en service au début de l'incident, entraîné sans lui
        rows = frame[(frame["timestamp"] > start - WINDOW) & (frame["timestamp"] <= start + WINDOW)]
        preds = predict(model, rows)
        for th, window in all_th:
            delay, by = None, None
            for k in range(7):
                run_at = start + RUN_HOUR + k * DAY
                overall, by_station = monitor.performance(judged_at(preds, run_at, window))
                found = monitor.alerts(
                    overall, by_station, th.ratio_max, th.station_ratio_max, th.station_min_predictions
                )
                if found:
                    delay = (run_at - start) / HOUR
                    by = "global" if found[0].startswith("ratio global") else "station"
                    break
            out.append({"scenario": scenario, "start": start, "thresholds": th.name, "delay_h": delay, "by": by})
    return pd.DataFrame(out)


def detection_summary(delays: pd.DataFrame) -> pd.DataFrame:
    g = delays.groupby(["scenario", "thresholds"])
    return pd.DataFrame(
        {
            "starts": g.size(),
            "detected_48h": g["delay_h"].apply(lambda d: round(float((d <= 48).mean()), 3)),
            "detected_7d": g["delay_h"].apply(lambda d: round(float(d.notna().mean()), 3)),
            "median_delay_h": g["delay_h"].median(),
        }
    ).reset_index()


# --- Rapport -------------------------------------------------------------------------------------


def report_markdown(results: list[dict], curve: pd.DataFrame, n_runs: int) -> str:
    def pct(x):
        return f"{100 * x:.0f} %"

    lines = [
        "# Backtest du monitoring (généré par `python -m velov.backtest`)",
        "",
        f"{n_runs} passages quotidiens sur l'historique non modifié ; scénarios injectés à chaque date de début "
        "possible, suivis 7 jours.",
        "",
        f"Demi-largeur médiane de l'IC à 95 % du ratio d'une station (bootstrap par blocs de {BLOCK_HOURS} h) :",
        "",
        "| n prédictions | " + " | ".join(str(n) for n in curve["n"]) + " |",
        "|---|" + "---|" * len(curve),
        "| demi-largeur | " + " | ".join(f"{w:.3f}" for w in curve["half_width_median"]) + " |",
        "",
        "| Fenêtre | Jeu de seuils | Global | Station | Min. n | Fausses alertes (global / station / total)"
        + "".join(f" | {s} : en 48 h | {s} : en 7 j | {s} : délai médian" for s in SCENARIOS)
        + " |",
        "|---|---|---|---|---|---|" + "---|---|---|" * len(SCENARIOS),
    ]
    for r in results:
        th, f, det = r["thresholds"], r["false_alarms"], r["detection"]
        row = (
            f"| {r['window_days']} j | {th.name} | {th.ratio_max:.2f} | {th.station_ratio_max:.2f} | "
            f"{th.station_min_predictions} | {pct(f['false_alarm_global'])} / {pct(f['false_alarm_station'])} / "
            f"{pct(f['false_alarm_any'])}"
        )
        for s in SCENARIOS:
            d = det[(det["scenario"] == s) & (det["thresholds"] == th.name)].iloc[0]
            delay = "—" if pd.isna(d["median_delay_h"]) else f"{d['median_delay_h']:.0f} h"
            row += f" | {pct(d['detected_48h'])} | {pct(d['detected_7d'])} | {delay}"
        lines.append(row + " |")
    return "\n".join(lines) + "\n"


def log_mlflow(results: list[dict], out: Path) -> None:
    import mlflow

    mlflow.set_experiment("velov-monitoring-backtest")
    for r in results:
        th = r["thresholds"]
        with mlflow.start_run(run_name=th.name):
            mlflow.log_params({"window_days": r["window_days"], **{k: v for k, v in asdict(th).items() if k != "name"}})
            metrics = dict(r["false_alarms"])
            for _, d in r["detection"][r["detection"]["thresholds"] == th.name].iterrows():
                metrics[f"{d['scenario']}_detected_48h"] = d["detected_48h"]
                metrics[f"{d['scenario']}_detected_7d"] = d["detected_7d"]
                if not pd.isna(d["median_delay_h"]):
                    metrics[f"{d['scenario']}_median_delay_h"] = float(d["median_delay_h"])
            mlflow.log_metrics(metrics)
            mlflow.log_artifacts(str(out))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Backtest et calibration des seuils de velov.monitor.")
    parser.add_argument("--data", type=Path, default=Path("data/velov_history.csv"))
    parser.add_argument("--out", type=Path, default=Path("docs/backtest"))
    parser.add_argument("--mlflow", action="store_true", help="un run par jeu de seuils (MLFLOW_TRACKING_URI)")
    args = parser.parse_args(argv)

    raw = load_table(args.data)
    raw["timestamp"] = pd.to_datetime(raw["timestamp"], utc=True)
    frame = make_training_frame(raw)
    start, end = raw["timestamp"].min(), raw["timestamp"].max()

    runs_at = run_times(start, end)
    first_cutoff = runs_at[0] - RUN_HOUR - WINDOW
    models = train_models(frame, list(pd.date_range(first_cutoff, end, freq=RETRAIN_EVERY)))
    print(f"{len(models)} modèles (réentraînement hebdomadaire), {len(runs_at)} passages normaux")

    # Seuils à évaluer : ceux du TP6 (fenêtre 7 jours), puis des seuils calibrés pour chaque fenêtre.
    candidates, normal, curve = [], {}, None
    for days in WINDOWS_DAYS:
        window = pd.Timedelta(days=days)
        runs, stations, errors = normal_runs(frame, models, runs_at, window)
        if days == 7:
            curve = min_predictions_curve(errors, [24, 48, 72, 96, 120, 144, 166])
            candidates.append((FIXED, window))
        normal[days] = (runs, stations)
    for days in WINDOWS_DAYS:
        window = pd.Timedelta(days=days)
        runs, stations = normal[days]
        candidates.append((calibrate(runs, stations, curve, window, f"calibrés {days} j"), window))

    # Débuts d'incident : chaque jour à minuit UTC, avec un modèle en service et 7 jours de suivi possibles.
    starts = list(pd.date_range(first_cutoff.ceil("D"), (end - WINDOW).floor("D"), freq="D"))
    delays = pd.concat([scenario_delays(raw, models, starts, s, candidates) for s in SCENARIOS], ignore_index=True)
    det = detection_summary(delays)

    results = []
    for th, window in candidates:
        runs, stations = normal[window.days]
        results.append(
            {
                "window_days": window.days,
                "thresholds": th,
                "false_alarms": false_alarms(runs, stations, th),
                "detection": det[det["thresholds"] == th.name],
            }
        )

    args.out.mkdir(parents=True, exist_ok=True)
    runs7, stations7 = normal[7]
    runs7.to_csv(args.out / "normal_runs_7j.csv", index=False)
    stations7.to_csv(args.out / "normal_stations_7j.csv", index=False)
    curve.to_csv(args.out / "min_predictions.csv", index=False)
    delays.to_csv(args.out / "scenario_delays.csv", index=False)
    summary = [
        {
            "window_days": r["window_days"],
            "thresholds": asdict(r["thresholds"]),
            "false_alarms": r["false_alarms"],
            "detection": r["detection"].to_dict(orient="records"),
        }
        for r in results
    ]
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    text = report_markdown(results, curve, len(runs_at))
    (args.out / "resultats.md").write_text(text, encoding="utf-8")
    print(text)
    if args.mlflow:
        log_mlflow(results, args.out)


if __name__ == "__main__":
    main()
