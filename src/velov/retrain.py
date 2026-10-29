"""Réentraînement planifié (S4 · 35) : entraîne sur la table actuelle, enregistre un challenger, puis
affiche la décision de promotion SANS rien changer. Un membre du binôme relit et lance `velov.promote --apply`.

--auto-promote applique la décision directement : à réserver au moment où la règle a tenu plusieurs cycles.

Crontab de la VM (chaque lundi à 6 h ; cron ne lit pas .bashrc, d'où les variables en tête) :
    MLFLOW_TRACKING_URI=http://127.0.0.1:5000
    0 6 * * 1  cd ~/velov-mlops && set -a && . ./.env && set +a && \
        DATABASE_URL=postgresql://velov:$POSTGRES_PASSWORD@localhost:5432/velov \
        .venv/bin/python -m velov.retrain --from-gold --version "$(date +\%Y.\%m.\%d)" >> logs/retrain.log 2>&1
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from mlflow import MlflowClient

from velov import promote, tracking
from velov.train import load_frame, temporal_split, train_frame


def main() -> None:
    parser = argparse.ArgumentParser(description="Réentraîne, enregistre un challenger, affiche la décision.")
    parser.add_argument("--data", type=Path, default=Path("data/velov_history.csv"))
    parser.add_argument("--from-gold", action="store_true", help="lire la table gold (DATABASE_URL)")
    parser.add_argument("--version", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--test-days", type=int, default=14)
    parser.add_argument("--auto-promote", action="store_true", help="appliquer la décision sans validation")
    args = parser.parse_args()

    frame, data_info = load_frame(args.data, os.environ["DATABASE_URL"] if args.from_gold else None)
    with tempfile.TemporaryDirectory() as out:  # le paquet part dans le run : rien à garder sur le disque
        metadata, pipeline = train_frame(frame, data_info, Path(out), args.version, args.test_days, args.seed)
        run_id, version = tracking.log_training(metadata, Path(out), pipeline, frame, register=True)
    print(f"Challenger enregistré : version {version} (run {run_id[:8]})")

    _, test_df = temporal_split(frame, args.test_days)
    client = MlflowClient()
    result = promote.compare(test_df, client)
    print(promote.report(result))
    if args.auto_promote:
        promote.apply(result, client)
    elif result["promote"]:
        print("Validation humaine attendue : python -m velov.promote --apply (même table, même fenêtre)")


if __name__ == "__main__":
    main()
