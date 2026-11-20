poste : Windows-11-10.0.26300-SP0, 20 cœurs logiques, Python 3.13.2, onnxruntime 1.31.0, pandas 3.0.2 ; n=300, échauffement=50

moteur onnx, ORT 1 thread(s), lot de 1 ligne(s), modèle 0.1.1
| étape | p50 ms | p95 ms | part du p50 | µs par ligne (p50) |
|---|---|---|---|---|
| 1. DataFrame | 0.597 | 0.837 | 2 % |  |
| 2. add_features | 2.710 | 3.130 | 9 % |  |
| 3. modèle | 3.780 | 4.400 | 13 % |  |
| 4. réponses | 0.083 | 0.119 | 0 % |  |
| 5. journalisation | 21.667 | 34.347 | 75 % |  |
| total | 28.852 | 41.670 |  |  |

contrôle : _predict() de l'API, une ligne : p50 5.852 ms, p95 6.660 ms
