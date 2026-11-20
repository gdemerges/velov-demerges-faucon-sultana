"""Courbe de saturation : débit et p95 de /v1/predict selon le nombre d'usagers, ligne du SLO à 200 ms.

    python scripts/plot_paliers.py results/w1 results/w2 --out docs/perf/saturation.png

Lit les mêmes fichiers que loadtest/summary.py (u<N>_stats.csv, ligne /v1/predict seule) ;
une configuration par dossier, superposées pour comparer.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROUTE = "/v1/predict"


def read(folder: Path) -> list[tuple[int, float, float]]:
    points = []
    for path in folder.glob("u*_stats.csv"):
        with path.open(newline="") as f:
            row = next((r for r in csv.DictReader(f) if r["Name"] == ROUTE), None)
        if row:
            users = int(re.findall(r"\d+", path.stem)[0])
            points.append((users, float(row["Requests/s"]), float(row["95%"])))
    return sorted(points)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dossiers", type=Path, nargs="+")
    parser.add_argument("--labels", nargs="+", help="une étiquette par dossier (défaut : nom du dossier)")
    parser.add_argument("--slo-ms", type=float, default=200.0)
    parser.add_argument("--out", type=Path, default=Path("docs/perf/saturation.png"))
    args = parser.parse_args()
    labels = args.labels or [d.name for d in args.dossiers]

    fig, (ax_rps, ax_p95) = plt.subplots(1, 2, figsize=(11, 4.2))
    for folder, label in zip(args.dossiers, labels, strict=True):
        pts = read(folder)
        users = [p[0] for p in pts]
        ax_rps.plot(users, [p[1] for p in pts], marker="o", label=label)
        ax_p95.plot(users, [p[2] for p in pts], marker="o", label=label)
    users_max = max(int(re.findall(r"\d+", p.stem)[0]) for d in args.dossiers for p in d.glob("u*_stats.csv"))
    ax_rps.plot([0, users_max], [0, users_max], ls=":", color="grey", label="plafond de Little (1 req/s/usager)")
    ax_rps.set(title="Débit de /v1/predict", xlabel="usagers simultanés", ylabel="req/s")
    ax_p95.axhline(args.slo_ms, color="red", ls="--", label=f"SLO p95 {args.slo_ms:.0f} ms")
    ax_p95.set(title="Latence p95 de /v1/predict", xlabel="usagers simultanés", ylabel="ms", yscale="log")
    for ax in (ax_rps, ax_p95):
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fig.tight_layout()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=120)
    print(f"Courbe écrite dans {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
