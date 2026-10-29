"""Export d'une version du registre vers le dossier que l'API charge (ADR-0004).

L'alias est résolu AVANT le build : le paquet (model.onnx + metadata.json) du run de la version est
téléchargé, son empreinte vérifiée, puis la version du registre est inscrite dans metadata.json.
GET /v1/model la renvoie : depuis l'API, on remonte au run, puis au commit et aux données (EX-13).

Usage :
    python -m velov.export --alias champion --out models/
    python -m velov.export --alias previous-champion --out models/    # retour arrière
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import mlflow
from mlflow import MlflowClient

from velov.artifact import METADATA_FILENAME, sha256_of
from velov.tracking import CHAMPION, MODEL_NAME


def export(alias: str, out_dir: Path, client: MlflowClient | None = None) -> dict:
    """Écrit le paquet de la version @alias dans out_dir. Retourne le bloc registry ajouté aux métadonnées.

    Raises:
        RuntimeError: si l'empreinte du modèle téléchargé ne correspond pas à son metadata.json.
    """
    client = client or MlflowClient()
    version = client.get_model_version_by_alias(MODEL_NAME, alias)
    with tempfile.TemporaryDirectory() as tmp:
        package = Path(
            mlflow.artifacts.download_artifacts(run_id=version.run_id, artifact_path="package", dst_path=tmp)
        )
        metadata = json.loads((package / METADATA_FILENAME).read_text(encoding="utf-8"))
        model_file = package / metadata["artifact"]["file"]
        if sha256_of(model_file) != metadata["artifact"]["sha256"]:
            raise RuntimeError(f"Empreinte invalide pour {model_file} (version {version.version})")
        registry = {
            "name": MODEL_NAME,
            "version": str(version.version),
            "alias": alias,
            "run_id": version.run_id,
            "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        metadata["registry"] = registry
        out_dir.mkdir(parents=True, exist_ok=True)
        # Seuls les fichiers servis : model.joblib (scikit-learn) reste dans le registre.
        shutil.copyfile(model_file, out_dir / model_file.name)
        (out_dir / METADATA_FILENAME).write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return registry


def main() -> None:
    parser = argparse.ArgumentParser(description="Exporte une version du registre pour l'API.")
    parser.add_argument("--alias", default=CHAMPION)
    parser.add_argument("--out", type=Path, default=Path("models"))
    args = parser.parse_args()
    registry = export(args.alias, args.out)
    print(
        f"{registry['name']} v{registry['version']} (@{args.alias}, run {registry['run_id'][:8]}) "
        f"exporté dans {args.out}"
    )


if __name__ == "__main__":
    main()
