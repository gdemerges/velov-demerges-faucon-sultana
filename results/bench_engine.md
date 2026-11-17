poste : Windows-11-10.0.26300-SP0, 20 cœurs logiques, Python 3.13.2, onnxruntime 1.31.0, pandas 3.0.2 ; n=500, échauffement=50

moteur joblib, lot de 1 ligne(s), modèle 0.1.1
| étape | p50 ms | p95 ms | part du p50 | µs par ligne (p50) |
|---|---|---|---|---|
| 1. DataFrame | 0.307 | 0.399 | 6 % |  |
| 2. add_features | 2.057 | 2.354 | 39 % |  |
| 3. modèle | 2.810 | 3.341 | 54 % |  |
| 4. réponses | 0.067 | 0.092 | 1 % |  |
| total | 5.237 | 6.160 |  |  |

moteur joblib, lot de 1000 ligne(s), modèle 0.1.1
| étape | p50 ms | p95 ms | part du p50 | µs par ligne (p50) |
|---|---|---|---|---|
| 1. DataFrame | 2.149 | 2.431 | 14 % | 2.1 |
| 2. add_features | 3.730 | 4.210 | 24 % | 3.7 |
| 3. modèle | 8.008 | 8.848 | 51 % | 8.0 |
| 4. réponses | 1.706 | 1.954 | 11 % | 1.7 |
| total | 15.602 | 17.316 |  | 15.6 |

moteur onnx, ORT 1 thread(s), lot de 1 ligne(s), modèle 0.1.1
| étape | p50 ms | p95 ms | part du p50 | µs par ligne (p50) |
|---|---|---|---|---|
| 1. DataFrame | 0.294 | 0.366 | 5 % |  |
| 2. add_features | 1.994 | 2.226 | 36 % |  |
| 3. modèle | 3.194 | 3.604 | 57 % |  |
| 4. réponses | 0.066 | 0.082 | 1 % |  |
| total | 5.574 | 6.147 |  |  |

moteur onnx, ORT 1 thread(s), lot de 1000 ligne(s), modèle 0.1.1
| étape | p50 ms | p95 ms | part du p50 | µs par ligne (p50) |
|---|---|---|---|---|
| 1. DataFrame | 2.128 | 2.466 | 14 % | 2.1 |
| 2. add_features | 3.698 | 4.279 | 25 % | 3.7 |
| 3. modèle | 7.054 | 7.881 | 48 % | 7.1 |
| 4. réponses | 1.822 | 2.066 | 12 % | 1.8 |
| total | 14.699 | 16.464 |  | 14.7 |

contrôle : _predict() de l'API, une ligne : p50 5.564 ms, p95 6.488 ms
