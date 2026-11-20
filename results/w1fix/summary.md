| usagers | requêtes | erreurs % | débit req/s | p50 ms | p95 ms | p99 ms | SLO p95 |
|---|---|---|---|---|---|---|---|
| 10 | 1015 | 0.0 | 8.6 | 59 | 66 | 85 | ok |
| 25 | 2516 | 0.0 | 21.2 | 60 | 71 | 88 | ok |
| 50 | 5016 | 0.0 | 42.3 | 62 | 84 | 120 | ok |
| 100 | 8858 | 0.0 | 74.2 | 170 | 610 | 920 | KO |
| 150 | 8880 | 0.0 | 74.2 | 900 | 1400 | 1500 | KO |

Capacité sous le SLO (p95 <= 200 ms) : environ 42 req/s (50 usagers). Le vrai plafond est entre ce palier et le suivant.
p99 peu fiable sous 10 000 requêtes : paliers [10, 25, 50, 100, 150].
