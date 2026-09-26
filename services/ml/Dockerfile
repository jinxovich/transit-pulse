# ML-сервис Transit Pulse. Контекст сборки — корень репозитория:
#   docker build -f services/ml/Dockerfile -t transit-pulse-ml .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

# Зависимости отдельным слоем: меняются реже кода.
COPY pyproject.toml uv.lock ./
COPY packages ./packages
RUN uv sync --frozen --no-dev --group service --group ml

COPY services/ml/app ./services/ml/app
COPY models ./models

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app \
    MODELS_DIR=/app/models

EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2).status != 200)"

CMD ["uvicorn", "services.ml.app.serve:app", "--host", "0.0.0.0", "--port", "8001"]
