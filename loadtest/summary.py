"""Résume les paliers d'un test de charge Locust : une ligne par palier, pour /v1/predict seul.

    python loadtest/summary.py results/w1 [--slo-ms 200] [--markdown]

Lit les fichiers u<N>_stats.csv écrits par loadtest/paliers.sh (ou par locust --csv results/w1/u<N>).
Ne regarde que /v1/predict : la ligne "Aggregated" mélange /health, qui ne coûte rien,
et embellit la latence.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

ROUTE = "/v1/predict"


def read_palier(path: Path) -> dict | None:
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            if row["Name"] == ROUTE:
                n = int(row["Request Count"])
                fails = int(row["Failure Count"])
                return {
                    "requetes": n,
                    "erreurs_pct": 100 * fails / n if n else 0.0,
                    "debit": float(row["Requests/s"]),
                    "p50": float(row["50%"]),
                    "p95": float(row["95%"]),
                    "p99": float(row["99%"]),
                }
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dossier", type=Path)
    parser.add_argument("--slo-ms", type=float, default=200.0, help="SLO de latence au p95 (défaut 200 ms)")
    parser.add_argument("--markdown", action="store_true", help="tableau Markdown, à coller dans le README")
    args = parser.parse_args()

    files = sorted(args.dossier.glob("u*_stats.csv"), key=lambda p: int(re.findall(r"\d+", p.stem)[0]))
    if not files:
        print(f"Aucun fichier u*_stats.csv dans {args.dossier}", file=sys.stderr)
        return 1

    rows = []
    for path in files:
        users = int(re.findall(r"\d+", path.stem)[0])
        palier = read_palier(path)
        if palier is None:
            print(f"{path.name} : pas de ligne {ROUTE}, palier ignoré", file=sys.stderr)
            continue
        rows.append({"usagers": users, **palier})

    head = ["usagers", "requêtes", "erreurs %", "débit req/s", "p50 ms", "p95 ms", "p99 ms", "SLO p95"]
    lines = []
    for r in rows:
        slo = "ok" if r["p95"] <= args.slo_ms and r["erreurs_pct"] < 1 else "KO"
        lines.append(
            [
                str(r["usagers"]),
                str(r["requetes"]),
                f"{r['erreurs_pct']:.1f}",
                f"{r['debit']:.1f}",
                f"{r['p50']:.0f}",
                f"{r['p95']:.0f}",
                f"{r['p99']:.0f}",
                slo,
            ]
        )
    if args.markdown:
        print("| " + " | ".join(head) + " |")
        print("|" + "---|" * len(head))
        for line in lines:
            print("| " + " | ".join(line) + " |")
    else:
        widths = [max(len(h), *(len(line[i]) for line in lines)) for i, h in enumerate(head)]
        print("  ".join(h.rjust(w) for h, w in zip(head, widths, strict=True)))
        for line in lines:
            print("  ".join(c.rjust(w) for c, w in zip(line, widths, strict=True)))

    ok = [r for r in rows if r["p95"] <= args.slo_ms and r["erreurs_pct"] < 1]
    if ok:
        best = max(ok, key=lambda r: r["debit"])
        print(f"\nCapacité sous le SLO (p95 <= {args.slo_ms:.0f} ms) : environ {best['debit']:.0f} req/s ", end="")
        print(f"({best['usagers']} usagers). Le vrai plafond est entre ce palier et le suivant.")
    else:
        print(f"\nAucun palier ne tient le SLO (p95 <= {args.slo_ms:.0f} ms) : commencer plus bas.")
    small = [r["usagers"] for r in rows if r["requetes"] < 10_000]
    if small:
        print(f"p99 peu fiable sous 10 000 requêtes : paliers {small}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
