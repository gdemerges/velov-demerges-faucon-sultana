# Docker : build, scan, publication

## Lancer
```bash
cp .env.example .env            # puis changer POSTGRES_PASSWORD
export VERSION=1.0.0 GIT_COMMIT=$(git rev-parse --short HEAD)
docker compose up -d --build    # API_PORT=8001 si le port 8000 est occupé
curl localhost:8000/ready
```
Image taguée `velov-api:<version>-<commit>` (labels OCI `version` et `revision`).

## Tailles d'images
| Image | Taille |
|---|---|
| Mono-étape (`python:3.12-slim`, avant) | 535 Mo |
| Multi-étape (actuelle, + psycopg) | 561 Mo |
| Base `python:3.12-slim` seule | ~130 Mo |
| Base `python:3.12-alpine` seule | 59 Mo |

Le multi-stage ne réduit pas la taille ici (pas de compilateur dans l'étape unique) ;
son intérêt est l'absence de pip/cache et de sources dans l'image finale. L'essentiel du poids
vient de scikit-learn, pandas et numpy. Alpine (musl) est déconseillé pour ces roues.

## Scan Trivy
```bash
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:latest \
  image --severity HIGH,CRITICAL --scanners vuln velov-api:1.0.0-<commit>
```
Résultat (2026-10-09) : 44 vulnérabilités HIGH, **toutes dans les paquets Debian de la
base** (util-linux, ncurses, perl-base...), aucune dans les dépendances Python. La base
`python:3.12-slim` seule en a 43 : c'est hérité, sans correctif disponible pour la plupart.
Action : reconstruire régulièrement pour récupérer les mises à jour de la base.

## Publication sur GHCR
```bash
echo $GITHUB_TOKEN | docker login ghcr.io -u gdemerges --password-stdin   # token write:packages
docker tag velov-api:1.0.0-<commit> ghcr.io/gdemerges/velov-api:1.0.0-<commit>
docker push ghcr.io/gdemerges/velov-api:1.0.0-<commit>
```
Attention : le modèle (`models/`) est embarqué dans l'image ; garder le paquet **privé**.
