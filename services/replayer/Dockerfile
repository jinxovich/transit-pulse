# Replayer: воспроизводит validate/traffic.csv как NDTP-поток для backend.
# Build context — корень репозитория (нужен общий пакет packages/transit_core):
#   docker build -f services/replayer/Dockerfile -t transit-replayer .
# Данные монтируются снаружи: -v ./data/raw:/app/data/raw:ro
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

COPY services/replayer ./services/replayer

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app/services/replayer \
    DATA_DIR=/app/data/raw \
    BACKEND_NDTP=backend:9201 \
    BACKEND_HTTP=http://backend:8000

EXPOSE 8090

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=2)"

CMD ["uvicorn", "replayer.main:app", "--host", "0.0.0.0", "--port", "8090"]
