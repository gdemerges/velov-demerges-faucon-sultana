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
| Multi-étape sans optimisation (+ psycopg) | 561 Mo |
| **Multi-étape optimisée (actuelle)** | **387 Mo** |
| Base `python:3.12-slim` seule | ~130 Mo |
| Base `python:3.12-alpine` seule | 59 Mo |

Gains (-31 %) : `uvicorn` sans l'extra `[standard]` (uvloop, httptools, watchfiles inutiles),
suppression de pip/setuptools, des dossiers `tests/`, `__pycache__` et fichiers `.pyi` du venv.
Le reste est incompressible : scipy, pandas, sklearn et numpy. Alpine est déconseillé (roues musl).

## Scan Trivy
```bash
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:latest \
  image --severity HIGH,CRITICAL --scanners vuln velov-api:1.0.0-<commit>
```
Résultat (2026-10-09) : 44 HIGH au départ, toutes dans les paquets Debian de la base
(aucune dans les dépendances Python), sans correctif publié par Debian.
Le Dockerfile applique `apt-get upgrade` (correctifs dès leur publication) et purge les outils
inutiles à une API (`mount`, `util-linux`, `bsdutils`, `ncurses-bin`, libs associées) avec
`dpkg --force-remove-essential` : **44 -> 19 HIGH**. Restent `login` (protégé par dpkg),
`libblkid1`, `libuuid1`, ncurses/systemd/acl/perl-base : sans correctif, risque faible
(conteneur non-root, ces outils ne sont jamais appelés). Reconstruire avec `--pull` régulièrement.

## Publication sur GHCR
```bash
echo $GITHUB_TOKEN | docker login ghcr.io -u gdemerges --password-stdin   # token write:packages
docker tag velov-api:1.0.0-<commit> ghcr.io/gdemerges/velov-api:1.0.0-<commit>
docker push ghcr.io/gdemerges/velov-api:1.0.0-<commit>
```
Attention : le modèle (`models/`) est embarqué dans l'image ; garder le paquet **privé**.
