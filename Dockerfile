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
COPY data_material/ ./data_material/
COPY scripts/ ./scripts/
COPY alembic.ini ./
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir --index-url=https://pypi.tuna.tsinghua.edu.cn/simple \
        --default-timeout=60 --retries=8 'setuptools>=80' wheel \
    && python -m pip install --no-cache-dir --no-build-isolation .

COPY --from=frontend-builder /app/web/dist ./web/dist
COPY logo.png ./logo.png
RUN mkdir -p /app/uploads /app/server/uploads

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && python scripts/import_dataagent_materials.py && exec python -m uvicorn server.main:app --host 0.0.0.0 --port 8000"]
