FROM python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MODEL_DIR=/app/models

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-deps .

COPY models ./models

RUN useradd --system --uid 10001 --no-create-home app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "velov.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
