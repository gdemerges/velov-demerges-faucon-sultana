# syntax=docker/dockerfile:1
# --- Étape 1 : build des dépendances dans un venv ---
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Deux environnements : /opt/venv (serving, part en production) et /opt/train (+ scikit-learn, skl2onnx),
# qui ne sert qu'à entraîner et exporter le modèle en ONNX ici, puis reste dans l'étape jetée (ADR-0003).
COPY requirements.txt requirements-train.txt ./
RUN pip install -r requirements.txt \
    && python -m venv /opt/train \
    && /opt/train/bin/pip install -r requirements-train.txt

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-deps . && /opt/train/bin/pip install --no-deps .

# Modèle entraîné dans l'image (données simulées, seed fixe) : models/ et data/ ne sont pas dans git,
# un clone propre doit pouvoir construire une image qui répond 200 sur /ready.
RUN /opt/train/bin/python -m velov.data --out /build/data/velov_history.csv \
    && /opt/train/bin/python -m velov.train --data /build/data/velov_history.csv --out /build/models

RUN pip uninstall -y pip setuptools wheel 2>/dev/null; \
    find /opt/venv -type d \( -name tests -o -name test -o -name __pycache__ \) -prune -exec rm -rf {} + ; \
    find /opt/venv -name '*.pyi' -delete

# --- Étape 2 : image d'exécution minimale ---
FROM python:3.12-slim

ARG VERSION=dev
ARG GIT_COMMIT=unknown
LABEL org.opencontainers.image.title="velov-api" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${GIT_COMMIT}"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MODEL_DIR=/app/models

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
# Seuls le graphe ONNX et ses métadonnées : model.joblib (scikit-learn) reste dans l'étape de build.
COPY --from=builder /build/models/model.onnx /build/models/metadata.json ./models/

# Mises à jour de sécurité de la base, puis retrait des outils système inutiles à une API
# (mount, login, util-linux, ncurses...) : moins de paquets = moins de CVE.
# --force-remove-essential : ces paquets sont "essentiels" pour apt mais pas pour Python.
RUN apt-get update \
    && apt-get -y --no-install-recommends upgrade \
    && useradd --system --uid 10001 --no-create-home app \
    && dpkg --purge --force-depends --force-remove-essential mount util-linux bsdutils ncurses-bin liblastlog2-2 \
        libmount1 libsmartcols1 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/ready')" || exit 1

CMD ["uvicorn", "velov.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
