# syntax=docker/dockerfile:1

FROM docker.m.daocloud.io/library/node:22.13-alpine AS frontend-builder

WORKDIR /app/web
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN npm install --global pnpm@11.24.0 --registry=https://registry.npmmirror.com \
    && pnpm config set registry https://registry.npmmirror.com \
    && pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM docker.m.daocloud.io/library/python:3.12-slim AS runtime

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PYTHONPATH=/app:/app/src

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY server/ ./server/
COPY migrations/ ./migrations/
COPY alembic.ini ./
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

COPY --from=frontend-builder /app/web/dist ./web/dist
COPY logo.png ./logo.png
RUN mkdir -p /app/uploads /app/server/uploads

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
