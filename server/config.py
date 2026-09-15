"""datadeck 服务配置（单源环境变量）。"""
from __future__ import annotations

import os

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


INSECURE_JWT_SECRET_VALUES = frozenset({
    "dev-insecure-secret-key-change-me",
    "please-change-to-a-random-string-at-least-32-characters",
})


class Settings(BaseSettings):
    # ── 应用 ──────────────────────────────────────────────
    app_name: str = "datadeck"
    environment: str = Field(default="development", validation_alias="DATADECK_ENV")
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

    # 本地能力测试开关：只跳过人工审批，不绕过用户/资源授权。
    # 生产环境必须保持 false；通过 DATADECK_AGENT_ALLOW_ALL_ACTIONS=true 开启。
    agent_allow_all_actions: bool = Field(
        default=False,
        validation_alias="DATADECK_AGENT_ALLOW_ALL_ACTIONS",
    )

    # ── 临时会话工作目录 ─────────────────────────────────
    temp_session_root: str = "/tmp/datadeck-sessions"
    temp_session_ttl_days: int = 7

    # ── 前端 ──────────────────────────────────────────────
    frontend_dist: str = os.path.join(os.path.dirname(__file__), "..", "web", "dist")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @model_validator(mode="after")
    def reject_unsafe_production_flags(self):
        environment = self.environment.strip().lower()
        if environment in {"prod", "production"}:
            if self.agent_allow_all_actions:
                raise ValueError("生产环境禁止 DATADECK_AGENT_ALLOW_ALL_ACTIONS=true")
            secret = self.jwt_secret_key.strip()
            if len(secret) < 32 or secret in INSECURE_JWT_SECRET_VALUES:
                raise ValueError("生产环境必须配置至少 32 字符且不是示例值的 JWT_SECRET_KEY")
        return self


settings = Settings()
