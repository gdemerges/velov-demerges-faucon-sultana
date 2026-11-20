"""Test de charge généré par un assistant IA (exercice d'audit, S5).

Demande faite à l'assistant : « écris un test de charge Locust pour mon API FastAPI de prédiction
(POST /v1/predict, GET /health) et dis-moi si elle tient un SLO de 200 ms ».

Le script tourne sans erreur et affiche « SLO respecté ». À vous de dire pourquoi ce verdict ne vaut rien.

    locust -f exercices/s5_audit/locustfile_ia.py --headless -u 100 -r 100 -t 20s
"""

from locust import HttpUser, constant, events, task

PAYLOAD = {
    "station_id": 1,
    "timestamp": "2026-11-17T08:00:00",
    "capacity": 20,
    "bikes_available": 10,
    "temperature": 12.0,
    "is_raining": False,
}


class ApiUser(HttpUser):
    host = "http://localhost:8000"
    wait_time = constant(0)

    @task
    def predict(self):
        with self.client.post("/v1/predict", json=PAYLOAD, catch_response=True) as response:
            # Évite que des erreurs ponctuelles faussent les statistiques
            response.success()

    @task
    def health(self):
        self.client.get("/health")


@events.test_stop.add_listener
def check_slo(environment, **kwargs):
    avg = environment.stats.total.avg_response_time
    print(f"Temps de réponse moyen : {avg:.1f} ms")
    if avg < 200:
        print("SLO respecté : l'API tient la charge")
    else:
        print("SLO non respecté")
