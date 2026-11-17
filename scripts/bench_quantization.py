"""TP5a : DistilBERT en float32 contre int8, le triplet mesuré (précision, latence, taille).

    pip install torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu
    pip install -r requirements-quantization.txt
    python scripts/bench_quantization.py [--n 200] [--out results/tp5a]

Modèle : distilbert-base-uncased-finetuned-sst-2-english. Données : SST-2, 872 phrases de validation
(nyu-mll/glue, fichier parquet, lu avec pandas). Trois variantes :
    float32          le modèle tel quel
    int8 torch.ao    torch.ao.quantization.quantize_dynamic sur les nn.Linear (poids int8, activations
                     quantifiées à la volée)
    int8 torchao     torchao.quantization.quantize_ avec la configuration int8 dynamique équivalente

Pour chacune : taille du state_dict sérialisé (Mo), latence d'UNE phrase de 128 tokens sur UN thread
(p50 et p95, échauffement non compté), précision sur SST-2 et accord avec float32 sur les mêmes phrases.
La latence se mesure sur un thread (torch.set_num_threads(1)), sinon elle dépend du poste ; la précision,
qui ne se chronomètre pas, est calculée avec tous les cœurs.

IA déclarée : écrit avec Claude Code (Opus 5.5) à partir de l'énoncé du TP5a ; vérifié en relisant les
prédictions float32 (précision attendue autour de 91 %, celle publiée pour ce modèle).
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import os
import platform
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from huggingface_hub import hf_hub_download
from transformers import AutoModelForSequenceClassification, AutoTokenizer

MODEL_ID = "distilbert-base-uncased-finetuned-sst-2-english"
SST2 = ("nyu-mll/glue", "sst2/validation-00000-of-00001.parquet")
SEQ_LEN = 128


def state_dict_mb(model: torch.nn.Module) -> float:
    buffer = io.BytesIO()
    torch.save(model.state_dict(), buffer)
    return buffer.getbuffer().nbytes / 1e6


def quantize_torch_ao(model: torch.nn.Module) -> torch.nn.Module:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)  # torch.ao.quantization est déprécié (S5 · 37)
        return torch.ao.quantization.quantize_dynamic(copy.deepcopy(model), {torch.nn.Linear}, dtype=torch.qint8)


def quantize_torchao(model: torch.nn.Module) -> torch.nn.Module:
    from torchao.quantization import quantize_

    try:  # API à base de configurations (torchao >= 0.10)
        from torchao.quantization import Int8DynamicActivationInt8WeightConfig as config_cls

        config = config_cls()
    except ImportError:  # anciennes versions
        from torchao.quantization import int8_dynamic_activation_int8_weight

        config = int8_dynamic_activation_int8_weight()
    out = copy.deepcopy(model)
    quantize_(out, config)
    return out


@torch.inference_mode()
def latency_ms(model, inputs: dict, n: int, warmup: int) -> list[float]:
    torch.set_num_threads(1)
    for _ in range(warmup):
        model(**inputs)
    out = []
    for _ in range(n):
        start = time.perf_counter()
        model(**inputs)
        out.append((time.perf_counter() - start) * 1000)
    return out


@torch.inference_mode()
def predict_labels(model, tokenizer, sentences: list[str], batch_size: int = 32) -> np.ndarray:
    torch.set_num_threads(os.cpu_count() or 1)
    preds = []
    for i in range(0, len(sentences), batch_size):
        enc = tokenizer(sentences[i : i + batch_size], padding=True, truncation=True, return_tensors="pt")
        preds.append(model(**enc).logits.argmax(dim=-1).numpy())
    return np.concatenate(preds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=200, help="appels chronométrés par variante")
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--out", type=Path, default=Path("results/tp5a"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    base = AutoModelForSequenceClassification.from_pretrained(MODEL_ID).eval()
    data = pd.read_parquet(hf_hub_download(SST2[0], SST2[1], repo_type="dataset"))
    sentences, labels = data["sentence"].tolist(), data["label"].to_numpy()

    # Une phrase de 128 tokens exactement : la plus longue du jeu, complétée par du padding.
    longest = max(sentences, key=len)
    one = tokenizer(longest, padding="max_length", max_length=SEQ_LEN, truncation=True, return_tensors="pt")

    variants = {"float32": lambda: base, "int8 torch.ao": lambda: quantize_torch_ao(base)}
    variants["int8 torchao"] = lambda: quantize_torchao(base)

    rows, ref = [], None
    for name, build in variants.items():
        try:
            model = build()
            lat = latency_ms(model, one, args.n, args.warmup)
            preds = predict_labels(model, tokenizer, sentences)
            size = state_dict_mb(model)
        except Exception as exc:  # une variante qui échoue est un résultat, pas un plantage du script
            rows.append({"variante": name, "erreur": f"{type(exc).__name__}: {exc}"[:200]})
            print(f"{name} : échec, {exc}")
            continue
        ref = preds if ref is None else ref
        row = {
            "variante": name,
            "taille_mo": round(size, 1),
            "p50_ms": round(float(np.percentile(lat, 50)), 1),
            "p95_ms": round(float(np.percentile(lat, 95)), 1),
            "precision_pct": round(100 * float((preds == labels).mean()), 2),
            "accord_float32_pct": round(100 * float((preds == ref).mean()), 2),
            "desaccords": int((preds != ref).sum()),
        }
        rows.append(row)
        print(row)

    env = {
        "poste": platform.platform(),
        "coeurs": os.cpu_count(),
        "python": platform.python_version(),
        "torch": torch.__version__,
        "threads_latence": 1,
        "seq_len": SEQ_LEN,
        "n": args.n,
        "warmup": args.warmup,
        "phrases": len(sentences),
    }
    (args.out / "resultats.json").write_text(json.dumps({"env": env, "rows": rows}, indent=2), encoding="utf-8")
    print("\n| variante | taille (Mo) | p50 (ms) | p95 (ms) | précision SST-2 | accord avec float32 |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        if "erreur" in r:
            print(f"| {r['variante']} | échec : {r['erreur']} | | | | |")
        else:
            print(
                f"| {r['variante']} | {r['taille_mo']} | {r['p50_ms']} | {r['p95_ms']} | {r['precision_pct']} % "
                f"| {r['accord_float32_pct']} % ({r['desaccords']} phrases) |"
            )
    print(f"\n{env}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
