# syntax=docker/dockerfile:1
# --- Étape 1 : build des dépendances dans un venv ---
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-deps . \
    && pip uninstall -y pip setuptools wheel 2>/dev/null; \
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
COPY models ./models

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
