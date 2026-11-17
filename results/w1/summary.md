| usagers | requêtes | erreurs % | débit req/s | p50 ms | p95 ms | p99 ms | SLO p95 |
|---|---|---|---|---|---|---|---|
| 10 | 651 | 0.0 | 5.5 | 700 | 1400 | 1700 | KO |
| 25 | 774 | 0.0 | 6.5 | 2600 | 3700 | 4600 | KO |
| 50 | 838 | 0.0 | 7.1 | 5500 | 7200 | 8000 | KO |
| 100 | 791 | 0.0 | 6.7 | 12000 | 17000 | 19000 | KO |
| 150 | 771 | 0.0 | 6.5 | 20000 | 24000 | 26000 | KO |

Aucun palier ne tient le SLO (p95 <= 200 ms) : commencer plus bas.
p99 peu fiable sous 10 000 requêtes : paliers [10, 25, 50, 100, 150].
