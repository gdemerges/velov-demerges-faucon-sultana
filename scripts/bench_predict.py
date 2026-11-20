"""Temps passé dans chaque étape de _predict(), sans réseau ni serveur HTTP (TP5, étape 3).

    python scripts/bench_predict.py --n 500                         # temps par étape, une ligne
    python scripts/bench_predict.py --n 500 --batch 1 100 1000      # selon la taille du lot
    python scripts/bench_predict.py --n 500 --ort-threads 0 1       # threads onnxruntime (0 = défaut ORT)
    python scripts/bench_predict.py --n 500 --cpus 1 --ort-threads 0 1   # idem, borné à un cœur (Linux)
    python scripts/bench_predict.py --n 200 --database-url postgresql://...   # + coût de la journalisation
    python scripts/bench_predict.py --n 500 --engine joblib onnx    # scikit-learn contre ONNX (EX-17)

Les quatre étapes sont celles de velov.api.main._predict, avec les mêmes fonctions importées (add_features,
FEATURES, onnx_feeds, PredictionResponse) : rien n'est réécrit, sinon on chronométrerait autre chose que l'API.
Le modèle est chargé AVANT la mesure, les --warmup premiers appels ne sont pas comptés, chaque appel est
chronométré avec time.perf_counter (pas time.time) et on rapporte p50 et p95, jamais un appel isolé.

IA déclarée : écrit avec Claude Code (Opus 5.5) à partir de l'énoncé du TP5 ; vérifié en comparant la somme des
étapes au temps de _predict() appelé directement (ligne « contrôle »).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import statistics
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import onnxruntime as ort
import pandas as pd

from velov.api.schemas import PredictionRequest, PredictionResponse
from velov.artifact import METADATA_FILENAME, onnx_feeds, onnx_session
from velov.features import FEATURES, add_features

STEPS = ["1. DataFrame", "2. add_features", "3. modèle", "4. réponses"]


def make_requests(n: int, seed: int = 0) -> list[PredictionRequest]:
    """Requêtes plausibles et toutes différentes, tirées comme dans loadtest/locustfile.py."""
    rng = random.Random(seed)
    capacity = {sid: rng.randint(15, 40) for sid in range(1, 21)}
    now = datetime(2026, 11, 17, 12, tzinfo=UTC)
    out = []
    for _ in range(n):
        sid = rng.randint(1, 20)
        out.append(
            PredictionRequest(
                station_id=sid,
                timestamp=now - timedelta(minutes=rng.randint(0, 30 * 24 * 60)),
                capacity=capacity[sid],
                bikes_available=rng.randint(0, capacity[sid]),
                temperature=round(min(max(rng.gauss(14, 7), -30), 50), 1),
                is_raining=rng.random() < 0.08,
            )
        )
    return out


def load_engine(model_dir: Path, engine: str, threads: int) -> tuple[Callable[[pd.DataFrame], np.ndarray], str]:
    metadata = json.loads((model_dir / METADATA_FILENAME).read_text(encoding="utf-8"))
    if engine == "onnx":
        session = onnx_session(model_dir / metadata["artifact"]["file"], threads)
        return (lambda X: session.run(None, onnx_feeds(X))[0].ravel()), metadata["model_version"]
    import joblib  # scikit-learn : pas dans l'image, seulement dans l'environnement d'entraînement

    model = joblib.load(model_dir / "model.joblib")
    return (lambda X: model.predict(X)), metadata["model_version"]


def time_steps(items: list[PredictionRequest], run_model, version: str, log=None) -> dict[str, float]:
    """Un appel de _predict(), étape par étape (en ms)."""
    t = [time.perf_counter()]
    raw = pd.DataFrame([item.model_dump() for item in items])
    t.append(time.perf_counter())
    features = add_features(raw)[FEATURES]
    t.append(time.perf_counter())
    preds = run_model(features)
    t.append(time.perf_counter())
    preds = np.clip(preds, 0.0, raw["capacity"].to_numpy())
    responses = [
        PredictionResponse(
            station_id=item.station_id,
            target_timestamp=item.timestamp + timedelta(hours=1),
            predicted_bikes=float(p),
            model_version=version,
        )
        for item, p in zip(items, preds, strict=True)
    ]
    t.append(time.perf_counter())
    out = {step: (t[i + 1] - t[i]) * 1000 for i, step in enumerate(STEPS)}
    if log is not None:
        start = time.perf_counter()
        log(items, responses)
        out["5. journalisation"] = (time.perf_counter() - start) * 1000
    return out


def pct(values: list[float], q: float) -> float:
    return float(np.percentile(values, q))


def bench(requests, batch: int, n: int, warmup: int, run_model, version: str, log=None) -> dict[str, list[float]]:
    samples: dict[str, list[float]] = {}
    for i in range(warmup + n):
        start = (i * batch) % max(len(requests) - batch, 1)
        timings = time_steps(requests[start : start + batch], run_model, version, log)
        if i >= warmup:
            for k, v in timings.items():
                samples.setdefault(k, []).append(v)
    samples["total"] = [sum(vals) for vals in zip(*samples.values(), strict=True)]
    return samples


def print_table(title: str, samples: dict[str, list[float]], batch: int, markdown: bool) -> None:
    total_p50 = statistics.median(samples["total"])
    rows = []
    for step, vals in samples.items():
        share = "" if step == "total" else f"{100 * statistics.median(vals) / total_p50:.0f} %"
        per_row = f"{statistics.median(vals) / batch * 1000:.1f}" if batch > 1 else ""
        rows.append([step, f"{pct(vals, 50):.3f}", f"{pct(vals, 95):.3f}", share, per_row])
    head = ["étape", "p50 ms", "p95 ms", "part du p50", "µs par ligne (p50)"]
    print(f"\n{title}")
    if markdown:
        print("| " + " | ".join(head) + " |\n|" + "---|" * len(head))
        for r in rows:
            print("| " + " | ".join(r) + " |")
    else:
        widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(head)]
        print("  ".join(h.ljust(w) for h, w in zip(head, widths, strict=True)))
        for r in rows:
            print("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=500, help="appels mesurés par configuration")
    parser.add_argument("--warmup", type=int, default=50, help="appels d'échauffement, non comptés")
    parser.add_argument("--batch", type=int, nargs="+", default=[1], help="tailles de lot")
    parser.add_argument("--ort-threads", type=int, nargs="+", default=[1], help="threads ORT (0 = défaut ORT)")
    parser.add_argument("--engine", nargs="+", choices=["onnx", "joblib"], default=["onnx"])
    parser.add_argument("--model-dir", type=Path, default=Path(os.getenv("MODEL_DIR", "models")))
    parser.add_argument("--cpus", type=int, help="borne le process à N cœurs (Linux : sched_setaffinity)")
    parser.add_argument("--database-url", help="mesure aussi la journalisation (velov.db.save_predictions)")
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args()

    if args.cpus:
        if not hasattr(os, "sched_setaffinity"):
            print("--cpus n'existe que sous Linux (ou dans le conteneur)", file=sys.stderr)
            return 1
        os.sched_setaffinity(0, set(range(args.cpus)))
    log = None
    if args.database_url:
        from velov import db

        db.init_db(args.database_url)
        log = lambda items, responses: db.save_predictions(args.database_url, items, responses)  # noqa: E731

    print(
        f"poste : {platform.platform()}, {os.cpu_count()} cœurs logiques, Python {platform.python_version()}, "
        f"onnxruntime {ort.__version__}, pandas {pd.__version__} ; n={args.n}, échauffement={args.warmup}"
    )
    requests = make_requests(max(args.batch) * 2 + 1000)
    for engine in args.engine:
        for threads in args.ort_threads if engine == "onnx" else [1]:
            run_model, version = load_engine(args.model_dir, engine, threads)  # chargé AVANT la mesure
            for batch in args.batch:
                samples = bench(requests, batch, args.n, args.warmup, run_model, version, log)
                label = f"moteur {engine}" + (f", ORT {threads or 'défaut'} thread(s)" if engine == "onnx" else "")
                print_table(f"{label}, lot de {batch} ligne(s), modèle {version}", samples, batch, args.markdown)

    # Contrôle : la somme des étapes doit retrouver le temps de _predict() appelé tel quel par l'API.
    from velov.api import main as api

    api.STATE["model"], api.STATE["metadata"] = api.load_model(args.model_dir)
    one = requests[:1]
    for _ in range(args.warmup):
        api._predict(one)
    durations = []
    for i in range(args.n):
        item = requests[i : i + 1]
        start = time.perf_counter()
        api._predict(item)
        durations.append((time.perf_counter() - start) * 1000)
    p50, p95 = pct(durations, 50), pct(durations, 95)
    print(f"\ncontrôle : _predict() de l'API, une ligne : p50 {p50:.3f} ms, p95 {p95:.3f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
