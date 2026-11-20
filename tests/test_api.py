"""Tests de l'API. Les fixtures (client, valid_payload, model_dir) sont dans conftest.py.

TODO 8 [Must] : faire passer les deux tests ci-dessous (une prédiction valide, une entrée invalide).
TODO 9 [Should] : en ajouter d'autres, par exemple :
  - /health renvoie 200 et /ready 503 quand MODEL_DIR pointe vers un dossier vide
  - un champ inconnu renvoie 422
  - un timestamp sans fuseau ("2026-10-06T08:00:00") renvoie 422
  - la prédiction est comprise entre 0 et capacity
  - (Stretch) une station jamais vue à l'entraînement ne fait pas planter l'API
"""

from datetime import UTC, datetime


def test_predict_valid(client, valid_payload):
    r = client.post("/v1/predict", json=valid_payload)
    assert r.status_code == 200
    # 8 h à Lyon (+02:00) + 1 h = 7 h UTC, quel que soit le fuseau dans lequel l'API répond
    target = datetime.fromisoformat(r.json()["target_timestamp"])
    assert target == datetime(2026, 10, 6, 7, tzinfo=UTC)


def test_predict_rejects_bikes_above_capacity(client, valid_payload):
    r = client.post("/v1/predict", json={**valid_payload, "bikes_available": 25})
    assert r.status_code == 422


# --- EX-01 : une entrée invalide renvoie 422, jamais 500 (vérifié en CI depuis S4) ---


def test_predict_rejects_unknown_field(client, valid_payload):
    assert client.post("/v1/predict", json={**valid_payload, "humidity": 80}).status_code == 422


def test_predict_rejects_missing_field(client, valid_payload):
    payload = {k: v for k, v in valid_payload.items() if k != "temperature"}
    assert client.post("/v1/predict", json=payload).status_code == 422


def test_predict_rejects_naive_timestamp(client, valid_payload):
    r = client.post("/v1/predict", json={**valid_payload, "timestamp": "2026-10-06T08:00:00"})
    assert r.status_code == 422


# --- EX-03 : la version du modèle dans chaque réponse ; prédiction bornée à [0, capacity] ---


def test_predict_returns_model_version_and_bounded_value(client, valid_payload):
    body = client.post("/v1/predict", json=valid_payload).json()
    assert body["model_version"] == client.get("/ready").json()["model_version"] == "0.0.0-test"
    assert 0 <= body["predicted_bikes"] <= valid_payload["capacity"]


def test_unknown_station_does_not_crash(client, valid_payload):
    assert client.post("/v1/predict", json={**valid_payload, "station_id": 9999}).status_code == 200


# --- EX-02 : sans modèle, le process vit (/health) mais n'est pas prêt (/ready, /v1/model, /v1/predict) ---


def test_without_model_health_ok_but_not_ready(tmp_path, monkeypatch, valid_payload):
    from fastapi.testclient import TestClient

    from velov.api.main import app

    monkeypatch.setenv("MODEL_DIR", str(tmp_path))  # dossier vide
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/ready").status_code == 503
        assert c.get("/v1/model").status_code == 503
        assert c.post("/v1/predict", json=valid_payload).status_code == 503


# --- TP5 : temps passé dans l'API, et prédictions par lot ---


def test_process_time_header(client, valid_payload):
    for r in (client.get("/ready"), client.post("/v1/predict", json=valid_payload)):
        assert float(r.headers["X-Process-Time-Ms"]) >= 0


def test_predict_batch_matches_unit_predictions(client, valid_payload):
    other = {**valid_payload, "station_id": 1, "bikes_available": 3}
    r = client.post("/v1/predict/batch", json={"items": [valid_payload, other]})
    assert r.status_code == 200
    batch = r.json()["predictions"]
    unit = [client.post("/v1/predict", json=p).json() for p in (valid_payload, other)]
    assert batch == unit


def test_predict_batch_rejects_empty_and_oversized(client, valid_payload):
    assert client.post("/v1/predict/batch", json={"items": []}).status_code == 422
    assert client.post("/v1/predict/batch", json={"items": [valid_payload] * 1001}).status_code == 422


# --- EX-20 : l'API expose ce que Prometheus lit (S6) ---


def test_metrics_expose_model_and_slo_bucket(client, valid_payload):
    assert client.post("/v1/predict", json=valid_payload).status_code == 200
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.text
    assert 'velov_model_info{model_version="0.0.0-test"} 1.0' in body
    # Le seuil du SLO (200 ms) doit être une borne d'histogramme, sinon le SLI ne se calcule pas (S6 · 14).
    assert 'http_request_duration_seconds_bucket{handler="/v1/predict",le="0.2",method="POST"}' in body
    assert 'http_requests_total{handler="/v1/predict",method="POST",status="2xx"}' in body


def test_metrics_route_not_instrumented_nor_documented(client):
    client.get("/metrics")
    assert 'handler="/metrics"' not in client.get("/metrics").text
    assert "/metrics" not in client.get("/openapi.json").json()["paths"]
