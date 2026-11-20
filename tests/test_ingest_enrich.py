"""Enrichissements de l'ingestion (référentiel des stations, prévision météo) : fonctions pures, sans réseau."""

from __future__ import annotations

from datetime import UTC, datetime

from velov import ingest


def test_forecast_keeps_next_hours_only():
    issued = datetime(2026, 11, 20, 10, tzinfo=UTC)
    payload = {
        "hourly": {
            "time": [
                "2026-11-20T10:00",
                "2026-11-20T11:00",
                "2026-11-20T12:00",
                "2026-11-20T13:00",
                "2026-11-20T14:00",
            ],
            "temperature_2m": [11.6, 12.0, 12.6, 13.0, 13.1],
            "precipitation": [0.0, 0.1, 0.2, 0.0, 0.0],
            "precipitation_probability": [5, 0, 10, 20, 30],
        }
    }
    rows = ingest.forecast_rows(payload, issued)
    # 10 h est le présent (déjà dans le relevé) ; on garde 11 h, 12 h et 13 h, publiées à 10 h.
    assert [r[1].hour for r in rows] == [11, 12, 13]
    assert all(r[0] == issued for r in rows)
    assert rows[0][2:] == (12.0, 0.1, 0)


def test_station_info_rows_from_feed():
    feed = [
        {
            "number": 9046,
            "name": "9046 - MANGINI",
            "lat": 45.79,
            "lng": 4.81,
            "commune": "Lyon 9e",
            "pole": " Logements ",
        },
        {"number": None, "name": "sans numéro"},
        {"number": 1001, "name": "1001 - X", "lat": 45.7, "lng": 4.8, "commune": "Lyon 1er", "pole": None},
    ]
    assert ingest.station_info_rows(feed) == [
        (9046, "9046 - MANGINI", 45.79, 4.81, "Lyon 9e", "Logements"),
        (1001, "1001 - X", 45.7, 4.8, "Lyon 1er", ""),
    ]


def test_enrichment_failure_never_raises(monkeypatch, caplog):
    def boom(*_):
        raise OSError("réseau coupé")

    monkeypatch.setattr(ingest, "load_station_info", boom)
    monkeypatch.setattr(ingest, "fill_elevations", boom)
    monkeypatch.setattr(ingest, "_get_json", boom)
    ingest.enrich("postgresql://inutile", [], datetime(2026, 11, 20, 10, tzinfo=UTC))  # ne lève rien
    assert caplog.text.count("échoué") == 3
