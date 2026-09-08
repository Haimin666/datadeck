"""datadeck 服务配置（单源环境变量）。"""
from __future__ import annotations

import os
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── 应用 ──────────────────────────────────────────────
    app_name: str = "datadeck"
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"

    # ── 数据库（PostgreSQL async） ─────────────────────────
    database_url: str = "postgresql+asyncpg://localhost:5432/datadeck"

    # ── JWT 认证 ──────────────────────────────────────────
    jwt_secret_key: str = os.getenv("JWT_SECRET_KEY", "dev-insecure-secret-key-change-me")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 天

    # ── 模型 ──────────────────────────────────────────────
    default_model: str = os.getenv("DATADECK_MODEL", "openai:gpt-4o")
    default_base_url: str = os.getenv("DATADECK_BASE_URL", "")
    default_api_key: str = os.getenv("DATADECK_API_KEY", "")

    # ── SSE ──────────────────────────────────────────────
    sse_heartbeat_seconds: int = 15
    sse_poll_interval_seconds: float = 1.0
    sse_max_connection_minutes: int = 30

    # ── 前端 ──────────────────────────────────────────────
    frontend_dist: str = os.path.join(os.path.dirname(__file__), "..", "web", "dist")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()


def require_jwt_secret() -> str:
    """生产环境强制要求 JWT_SECRET_KEY 长度 ≥ 32。"""
    if len(settings.jwt_secret_key) < 32:
        env = os.getenv("DATADECK_ENV", "development").strip().lower()
        if env in ("prod", "production"):
            raise RuntimeError(
                "JWT_SECRET_KEY 未配置，请在生产环境 .env 中设置至少 32 字符的随机字符串"
            )
    return settings.jwt_secret_key
