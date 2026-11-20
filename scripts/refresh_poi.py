"""Régénère dbt/seeds/poi_lyon.csv depuis OpenStreetMap (Overpass) : gares et établissements d'enseignement supérieur.

Données © les contributeurs OpenStreetMap, licence ODbL (https://www.openstreetmap.org/copyright).
Le fichier est versionné : dbt ne dépend pas du réseau, et un changement d'OSM se voit dans le diff.

    python scripts/refresh_poi.py
    python scripts/refresh_poi.py --from-file reponse_overpass.json   # sans réseau
"""

from __future__ import annotations

import argparse
import csv
import json
import urllib.parse
import urllib.request
from pathlib import Path

BBOX = "45.65,4.75,45.85,5.0"  # métropole de Lyon (sud, ouest, nord, est)
QUERY = f"""[out:json][timeout:60];
(node["railway"="station"]["train"="yes"]({BBOX});
 nwr["amenity"="university"]({BBOX}););
out center tags;"""
OUT = Path(__file__).resolve().parents[1] / "dbt" / "seeds" / "poi_lyon.csv"


def fetch() -> dict:
    data = urllib.parse.urlencode({"data": QUERY}).encode()
    req = urllib.request.Request(
        "https://overpass-api.de/api/interpreter", data=data, headers={"User-Agent": "velov-mlops-student/1.0"}
    )
    with urllib.request.urlopen(req, timeout=90) as resp:  # noqa: S310 (URL https constante)
        return json.load(resp)


def rows(payload: dict) -> list[dict]:
    out = {}
    for e in payload["elements"]:
        tags = e.get("tags", {})
        lat = e.get("lat", e.get("center", {}).get("lat"))
        lon = e.get("lon", e.get("center", {}).get("lon"))
        if lat is None or lon is None:
            continue
        kind = "gare" if tags.get("railway") == "station" else "universite"
        key = f"{e['type']}/{e['id']}"
        out[key] = {
            "osm_id": key,
            "kind": kind,
            "name": tags.get("name", ""),
            "lat": round(lat, 6),
            "lon": round(lon, 6),
        }
    return sorted(out.values(), key=lambda r: (r["kind"], r["name"], r["osm_id"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from-file", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.from_file.read_text()) if args.from_file else fetch()
    data = rows(payload)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["osm_id", "kind", "name", "lat", "lon"])
        writer.writeheader()
        writer.writerows(data)
    gares = sum(r["kind"] == "gare" for r in data)
    print(f"{OUT} : {gares} gares, {len(data) - gares} universités")


if __name__ == "__main__":
    main()
