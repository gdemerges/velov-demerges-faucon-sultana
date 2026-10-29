"""Promotion d'un modèle du registre : la règle est écrite (et testée) avant de regarder les chiffres.

Trois conditions, vérifiées dans cet ordre (S4 · 23) :
1. Même fenêtre : challenger et champion sont évalués sur les mêmes `test_days` derniers jours de la table
   actuelle. On recharge les modèles depuis le registre au lieu de relire leurs métriques d'entraînement,
   calculées sur une autre période (un champion vieillit).
2. Battre la persistance (EX-05) : sinon le modèle ne justifie pas sa complexité.
3. Battre le champion d'au moins MIN_GAIN (2 %) : entre deux seeds, la MAE varie de ~1,6 % (README, TP4) ;
   un gain plus petit peut venir du hasard seul.

Par défaut, la décision est affichée sans rien changer ; un membre du binôme relance avec --apply (S4 · 25).

Usage :
    python -m velov.promote --data data/velov_history.csv            # décision seule
    python -m velov.promote --data data/velov_history.csv --apply    # déplace les alias
    python -m velov.promote --rollback                                # champion <- previous-champion
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

import mlflow
import pandas as pd
from mlflow import MlflowClient
from mlflow.exceptions import MlflowException

from velov.tracking import CHALLENGER, CHAMPION, MODEL_NAME, PREVIOUS_CHAMPION
from velov.train import evaluate, load_frame, temporal_split

MIN_GAIN = 0.02


def decide(challenger: dict, champion: dict | None, min_gain: float = MIN_GAIN) -> tuple[bool, str, float | None]:
    """Applique la règle de promotion. Retourne (promouvoir, raison, seuil de MAE à atteindre ou None).

    challenger et champion : métriques de velov.train.evaluate, plus la clé "window" (fenêtre d'évaluation).
    """
    if champion is not None and challenger["window"] != champion["window"]:
        return False, "fenêtres d'évaluation différentes : comparaison invalide", None
    if challenger["mae_model"] >= challenger["mae_baseline_persistence"]:
        return (
            False,
            (
                f"ne bat pas la persistance (MAE {challenger['mae_model']:.3f} >= "
                f"{challenger['mae_baseline_persistence']:.3f})"
            ),
            None,
        )
    if champion is None:
        return True, "premier champion (bat la persistance)", None
    target = champion["mae_model"] * (1 - min_gain)
    gain = challenger["mae_model"] <= target
    op = "<=" if gain else ">"
    reason = f"MAE {challenger['mae_model']:.3f} {op} {target:.3f} (champion - {min_gain:.0%})"
    return gain, ("gain sur le champion : " if gain else "gain insuffisant : ") + reason, target


def version_for(client: MlflowClient, alias: str):
    """La version du registre qui porte cet alias, ou None."""
    try:
        return client.get_model_version_by_alias(MODEL_NAME, alias)
    except MlflowException:
        return None


def evaluate_version(version, test_df: pd.DataFrame) -> dict:
    model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}/{version.version}")
    return evaluate(model, test_df)


def window_of(test_df: pd.DataFrame) -> dict:
    ts = pd.to_datetime(test_df["timestamp"])
    return {"start": ts.min().isoformat(), "end": ts.max().isoformat(), "n_rows": int(len(test_df))}


def compare(test_df: pd.DataFrame, client: MlflowClient, min_gain: float = MIN_GAIN) -> dict:
    """Évalue challenger, champion et persistance sur la même fenêtre, puis applique la règle."""
    challenger = version_for(client, CHALLENGER)
    if challenger is None:
        raise SystemExit(f"Aucune version @{CHALLENGER} : lancer `python -m velov.train --mlflow --register`")
    champion = version_for(client, CHAMPION)
    window = window_of(test_df)

    c_metrics = {**evaluate_version(challenger, test_df), "window": window}
    if champion is not None and champion.version == challenger.version:
        return {
            "challenger": challenger,
            "champion": champion,
            "metrics": c_metrics,
            "champion_metrics": c_metrics,
            "window": window,
            "promote": False,
            "reason": "le challenger est déjà champion",
            "target": None,
        }
    k_metrics = {**evaluate_version(champion, test_df), "window": window} if champion is not None else None
    promote, reason, target = decide(c_metrics, k_metrics, min_gain)
    return {
        "challenger": challenger,
        "champion": champion,
        "metrics": c_metrics,
        "champion_metrics": k_metrics,
        "window": window,
        "promote": promote,
        "reason": reason,
        "target": target,
    }


def apply(result: dict, client: MlflowClient) -> None:
    """Déplace les alias : l'ancien champion devient previous-champion. Trace la décision sur la version."""
    challenger, champion = result["challenger"], result["champion"]
    now = datetime.now(UTC).isoformat(timespec="seconds")
    tag = "promotion" if result["promote"] else "promotion_refusee"
    client.set_model_version_tag(MODEL_NAME, challenger.version, tag, f"{now} : {result['reason']}")
    client.set_model_version_tag(
        MODEL_NAME, challenger.version, "mae_window", f"{result['metrics']['mae_model']} sur {result['window']}"
    )
    if not result["promote"]:
        return
    if champion is not None:
        client.set_registered_model_alias(MODEL_NAME, PREVIOUS_CHAMPION, champion.version)
    client.set_registered_model_alias(MODEL_NAME, CHAMPION, challenger.version)


def rollback(client: MlflowClient) -> tuple[str, str]:
    """Revient au champion précédent ; l'ancien champion prend l'alias previous-champion (rejouable)."""
    previous, current = version_for(client, PREVIOUS_CHAMPION), version_for(client, CHAMPION)
    if previous is None:
        raise SystemExit(f"Aucune version @{PREVIOUS_CHAMPION} : rien vers quoi revenir")
    client.set_registered_model_alias(MODEL_NAME, CHAMPION, previous.version)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    client.set_model_version_tag(MODEL_NAME, previous.version, "rollback", f"{now} : remis en champion")
    if current is not None:
        client.set_registered_model_alias(MODEL_NAME, PREVIOUS_CHAMPION, current.version)
    return str(previous.version), str(current.version) if current is not None else "-"


def report(result: dict) -> str:
    w, m, k = result["window"], result["metrics"], result["champion_metrics"]
    lines = [
        f"Fenêtre de test : {w['start']} -> {w['end']} UTC, {w['n_rows']} lignes",
        f"challenger v{result['challenger'].version} : MAE {m['mae_model']:.3f} "
        f"(heures de pointe {m['mae_model_peak_hours']:.3f})",
    ]
    if k is not None:
        lines.append(
            f"champion v{result['champion'].version}   : MAE {k['mae_model']:.3f} "
            f"(heures de pointe {k['mae_model_peak_hours']:.3f})"
        )
    lines.append(f"persistance     : MAE {m['mae_baseline_persistence']:.3f}")
    lines.append(f"Décision : {'PROMOUVOIR' if result['promote'] else 'GARDER LE CHAMPION'} ({result['reason']})")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare challenger et champion, et applique la règle.")
    parser.add_argument("--data", type=Path, default=Path("data/velov_history.csv"))
    parser.add_argument("--from-gold", action="store_true", help="évaluer sur la table gold (DATABASE_URL)")
    parser.add_argument("--test-days", type=int, default=14)
    parser.add_argument("--min-gain", type=float, default=MIN_GAIN)
    parser.add_argument("--apply", action="store_true", help="déplacer les alias selon la décision")
    parser.add_argument("--rollback", action="store_true", help="remettre @previous-champion en @champion")
    args = parser.parse_args()

    client = MlflowClient()
    if args.rollback:
        restored, demoted = rollback(client)
        print(f"Retour arrière : champion = v{restored} (v{demoted} devient {PREVIOUS_CHAMPION})")
        return

    frame, _ = load_frame(args.data, os.environ["DATABASE_URL"] if args.from_gold else None)
    _, test_df = temporal_split(frame, args.test_days)
    result = compare(test_df, client, args.min_gain)
    print(report(result))
    if args.apply:
        apply(result, client)
        print("Alias mis à jour." if result["promote"] else "Décision tracée sur la version, alias inchangés.")
    elif result["promote"]:
        print("Rien n'a changé : relancer avec --apply pour promouvoir.")


if __name__ == "__main__":
    main()
