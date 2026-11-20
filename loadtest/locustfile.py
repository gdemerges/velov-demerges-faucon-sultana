"""Test de charge de l'API Vélo'v (S5, TP5).

Des usagers virtuels imitent l'application : beaucoup de prédictions, quelques appels de santé.
Chaque requête est différente (station, instant, remplissage, météo tirés au hasard), sinon on
mesure un cas unique et pas le service (polycopié S5, protocole de mesure).

Lancement, DEPUIS VOTRE POSTE, vers l'API sur la VM (jamais sur la machine qui sert l'API) :
    locust -f loadtest/locustfile.py --host http://<ip_vm>:8000 \
        --headless -u 50 -r 10 -t 2m --csv results/run_u50 --only-summary

Interface web (courbes en direct) : même commande sans --headless, puis http://localhost:8089.
Pour une courbe de saturation complète : loadtest/paliers.sh, puis loadtest/summary.py.

Variables d'environnement :
    VELOV_STATIONS   nombre de stations tirées au hasard (défaut 20, comme l'historique simulé)
"""

from __future__ import annotations

import os
import random
from datetime import UTC, datetime, timedelta

from locust import HttpUser, between, task

N_STATIONS = int(os.getenv("VELOV_STATIONS", "20"))
if N_STATIONS < 1:
    raise ValueError("VELOV_STATIONS doit être >= 1")

# Une capacité fixe par station, comme dans la réalité (tirée une fois, graine fixe).
_rng = random.Random(42)
CAPACITY = {sid: _rng.randint(15, 40) for sid in range(1, N_STATIONS + 1)}


def random_payload() -> dict:
    """Une observation plausible : instant des 30 derniers jours, en UTC avec fuseau explicite."""
    station_id = random.randint(1, N_STATIONS)
    capacity = CAPACITY[station_id]
    instant = datetime.now(UTC) - timedelta(minutes=random.randint(0, 30 * 24 * 60))
    return {
        "station_id": station_id,
        "timestamp": instant.replace(second=0, microsecond=0).isoformat(),
        "capacity": capacity,
        "bikes_available": random.randint(0, capacity),
        "temperature": round(min(max(random.gauss(14, 7), -30), 50), 1),
        "is_raining": random.random() < 0.08,
    }


class AppUser(HttpUser):
    # Temps de réflexion entre deux requêtes d'un même usager.
    wait_time = between(0.5, 1.5)

    @task(10)
    def predict(self) -> None:
        # Toute réponse >= 400 compte comme un échec dans les statistiques de Locust.
        self.client.post("/v1/predict", json=random_payload(), name="/v1/predict")

    @task(1)
    def health(self) -> None:
        self.client.get("/health", name="/health")
