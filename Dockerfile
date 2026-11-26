# syntax=docker/dockerfile:1

# 1. Build the React frontend
FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# 2. Resolve Python dependencies into /app/.venv with uv
FROM python:3.12-slim AS python-deps
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev

# 3. Slim runtime: venv + app code + built frontend, non-root
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"
RUN groupadd --system app && useradd --system --gid app --home-dir /app app
WORKDIR /app
COPY --from=python-deps /app/.venv ./.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY --from=frontend /build/dist ./frontend/dist
USER app
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec python -m app --host 0.0.0.0"]
