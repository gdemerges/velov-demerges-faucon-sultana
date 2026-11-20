poste : Linux-6.18.58.1-microsoft-standard-WSL2-x86_64-with-glibc2.41, 20 cœurs logiques, Python 3.12.16, onnxruntime 1.31.0, pandas 3.0.2 ; n=300, échauffement=50

moteur onnx, ORT 1 thread(s), lot de 1 ligne(s), modèle 0.1.0
| étape | p50 ms | p95 ms | part du p50 | µs par ligne (p50) |
|---|---|---|---|---|
| 1. DataFrame | 0.271 | 0.336 | 8 % |  |
| 2. add_features | 1.388 | 1.599 | 40 % |  |
| 3. modèle | 1.710 | 2.050 | 50 % |  |
| 4. réponses | 0.056 | 0.079 | 2 % |  |
| total | 3.443 | 3.860 |  |  |

moteur onnx, ORT 1 thread(s), lot de 100 ligne(s), modèle 0.1.0
| étape | p50 ms | p95 ms | part du p50 | µs par ligne (p50) |
|---|---|---|---|---|
| 1. DataFrame | 0.455 | 0.549 | 11 % | 4.6 |
| 2. add_features | 1.496 | 1.733 | 36 % | 15.0 |
| 3. modèle | 2.008 | 2.305 | 48 % | 20.1 |
| 4. réponses | 0.220 | 0.260 | 5 % | 2.2 |
| total | 4.195 | 4.856 |  | 41.9 |

moteur onnx, ORT 1 thread(s), lot de 1000 ligne(s), modèle 0.1.0
| étape | p50 ms | p95 ms | part du p50 | µs par ligne (p50) |
|---|---|---|---|---|
| 1. DataFrame | 2.000 | 2.380 | 18 % | 2.0 |
| 2. add_features | 2.510 | 2.927 | 22 % | 2.5 |
| 3. modèle | 5.054 | 5.797 | 45 % | 5.1 |
| 4. réponses | 1.777 | 2.000 | 16 % | 1.8 |
| total | 11.336 | 14.817 |  | 11.3 |

contrôle : _predict() de l'API, une ligne : p50 88.905 ms, p95 192.071 ms
