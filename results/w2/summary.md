| usagers | requêtes | erreurs % | débit req/s | p50 ms | p95 ms | p99 ms | SLO p95 |
|---|---|---|---|---|---|---|---|
| 10 | 950 | 0.0 | 8.7 | 59 | 65 | 70 | ok |
| 25 | 2349 | 0.0 | 21.0 | 59 | 68 | 100 | ok |
| 100 | 537 | 0.0 | 66.7 | 69 | 230 | 360 | KO |

Capacité sous le SLO (p95 <= 200 ms) : environ 21 req/s (25 usagers). Le vrai plafond est entre ce palier et le suivant.
p99 peu fiable sous 10 000 requêtes : paliers [10, 25, 100].
