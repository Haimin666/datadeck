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
    PIP_CACHE_DIR=/root/.cache/pip \
    PYTHONPATH=/app:/app/src

# 代码仓库手动拉取所需；不安装 shell 执行环境以外的额外构建工具。
# Debian 基础镜像默认源切换到国内镜像，避免服务器无法访问 deb.debian.org。
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i 's|deb.debian.org/debian|mirrors.aliyun.com/debian|g; s|security.debian.org/debian-security|mirrors.aliyun.com/debian-security|g' /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
# 先用最小包骨架安装并缓存第三方依赖，Agent/RAG 源码变化不再触发全量下载。
RUN mkdir -p src/datadeck && touch src/datadeck/__init__.py
RUN python -m pip install --upgrade pip \
    && python -m pip install --index-url=https://pypi.tuna.tsinghua.edu.cn/simple \
        --default-timeout=120 --retries=12 'setuptools>=80' wheel \
    && python -m pip install --no-build-isolation .

# 业务代码、迁移和可注入物料放在依赖层之后；本包重装不解析依赖。
COPY src/ ./src/
RUN python -m pip install --no-deps --no-build-isolation .
COPY server/ ./server/
COPY migrations/ ./migrations/
COPY data_material/ ./data_material/
COPY scripts/ ./scripts/
COPY alembic.ini ./

COPY --from=frontend-builder /app/web/dist ./web/dist
COPY logo.png ./logo.png
RUN mkdir -p /app/uploads

EXPOSE 8000
# 启动只负责迁移和提供服务；大批量物料注入由 docker-manage.sh 在健康检查
# 通过后显式执行，避免每次重启阻塞应用启动或因物料问题导致容器反复重启。
CMD ["sh", "-c", "alembic upgrade head && exec python -m uvicorn server.main:app --host 0.0.0.0 --port 8000"]
