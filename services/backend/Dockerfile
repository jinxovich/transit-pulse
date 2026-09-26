# Backend: ingest NDTP (TCP 9201) + REST/WS API (8000) + оркестрация прогнозов.
# Build context — корень репозитория (нужен общий пакет packages/transit_core):
#   docker build -f services/backend/Dockerfile -t transit-pulse-backend .
# Данные монтируются снаружи: -v ./data/raw:/data/raw:ro -e DATA_DIR=/data/raw
# Тот же образ использует data-init: python -m scripts.data_prep --dest /data/raw
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages ./packages
RUN uv sync --frozen --no-dev --group service

COPY scripts ./scripts
COPY contracts ./contracts
COPY models ./models
COPY services/backend ./services/backend

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app:/app/services/backend \
    DATA_DIR=/data/raw \
    MODELS_DIR=/app/models \
    ML_URL=http://ml:8001 \
    REPLAYER_URL=http://replayer:8090 \
    NDTP_PORT=9201

EXPOSE 8000 9201

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
