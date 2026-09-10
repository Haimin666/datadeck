"""datadeck 数据库模型（精简版，仅 M1 所需）。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey, Identity, Integer, String,
    Text, UniqueConstraint, Index, Float, func
)
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import DeclarativeBase

from server.utils.datetime_utils import utc_now_naive


class Base(DeclarativeBase):
    pass


# ── User ──────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(128), nullable=False, unique=True, index=True)
    uid = Column(String(64), nullable=False, unique=True, index=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(32), nullable=False, default="user")  # user / admin / superadmin
    domain = Column(String(64), nullable=False, default="default", server_default="default")  # 业务域隔离
    avatar = Column(String(512), nullable=True)  # 头像 URL（上传后回填）
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    last_login = Column(DateTime, nullable=True)
    is_deleted = Column(Integer, nullable=False, default=0)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "uid": self.uid,
            "role": self.role,
            "avatar": self.avatar or "",
            "domain": self.domain or "default",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
        }


# ── Thread (对话线程) ──────────────────────────────────────
class Thread(Base):
    __tablename__ = "threads"
    __table_args__ = (
        # uid 列已有 index=True（自动 ix_threads_uid），勿重复声明同名索引
        Index("ix_threads_agent_id", "agent_id"),
    )

    id = Column(String(64), primary_key=True)
    uid = Column(String(64), nullable=False, index=True)
    agent_id = Column(String(128), nullable=False)
    project_id = Column(String(64), nullable=True, index=True)
    title = Column(String(256), nullable=False, default="新的对话")
    status = Column(String(32), nullable=False, default="active", server_default="active", index=True)
    extra_metadata = Column(JSON, nullable=False, default=dict, server_default="{}")
    is_pinned = Column(Boolean, nullable=False, default=False)
    tool_approval_mode = Column(String(32), nullable=False, default="default",
                                server_default="default")  # default/always_trust/none
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False)
    viewed_at = Column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "uid": self.uid,
            "agent_id": self.agent_id,
            "project_id": self.project_id,
            "title": self.title,
            "status": self.status or "active",
            "extra_metadata": self.extra_metadata or {},
            "metadata": self.extra_metadata or {},
            "is_pinned": self.is_pinned,
            "tool_approval_mode": self.tool_approval_mode or "default",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "viewed_at": self.viewed_at.isoformat() if self.viewed_at else None,
        }


# ── Knowledge base ────────────────────────────────────────
class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id = Column(String(64), primary_key=True)
    uid = Column(String(64), ForeignKey("users.uid", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=False, default="", server_default="")
    embedding_model = Column(String(256), nullable=False, default="BAAI/bge-m3", server_default="BAAI/bge-m3")
    collection_name = Column(String(128), nullable=False, unique=True)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False)

    def to_dict(self, *, document_count: int = 0, chunk_count: int = 0) -> dict:
        return {
            "id": self.id,
            "kb_id": self.id,
            "uid": self.uid,
            "name": self.name,
            "description": self.description or "",
            "embedding_model": self.embedding_model,
            "collection_name": self.collection_name,
            "document_count": document_count,
            "chunk_count": chunk_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (Index("ix_knowledge_documents_kb_status", "kb_id", "status"),)

    id = Column(String(64), primary_key=True)
    kb_id = Column(String(64), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(512), nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="pending", server_default="pending")
    chunk_count = Column(Integer, nullable=False, default=0, server_default="0")
    error_message = Column(Text, nullable=False, default="", server_default="")
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "document_id": self.id,
            "kb_id": self.kb_id,
            "filename": self.filename,
            "status": self.status,
            "chunk_count": self.chunk_count,
            "error_message": self.error_message or "",
            "size": len(self.content.encode("utf-8")),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ── Project ───────────────────────────────────────────────
class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("id", "uid", name="uq_projects_id_uid"),
        UniqueConstraint("uid", "idempotency_key", name="uq_projects_uid_idempotency_key"),
    )

    id = Column(String(64), primary_key=True)
    uid = Column(String(64), ForeignKey("users.uid", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=True)
    selection_status = Column(String(20), nullable=False, index=True)
    workdir_path = Column(String(512), nullable=False)
    directory_mode = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="active", server_default="active", index=True)
    deleted_at = Column(DateTime, nullable=True)
    idempotency_key = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, server_default=func.now(), nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "uid": self.uid,
            "name": self.name,
            "selection_status": self.selection_status,
            "workdir_path": self.workdir_path,
            "directory_mode": self.directory_mode,
            "status": self.status,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ── Skill ──────────────────────────────────────────────────
class Skill(Base):
    __tablename__ = "skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(128), nullable=False, unique=True, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=False)
    source_type = Column(String(32), nullable=False, default="upload", index=True)
    tool_dependencies = Column(JSON, nullable=False, default=list)
    mcp_dependencies = Column(JSON, nullable=False, default=list)
    skill_dependencies = Column(JSON, nullable=False, default=list)
    dir_path = Column(String(512), nullable=False)
    version = Column(String(64), nullable=True)
    content_hash = Column(String(128), nullable=True)
    share_config = Column(JSON, nullable=False, default=dict)
    enabled = Column(Boolean, nullable=False, default=True)
    created_by = Column(String(64), nullable=True)
    updated_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "source_type": self.source_type,
            "tool_dependencies": self.tool_dependencies or [],
            "mcp_dependencies": self.mcp_dependencies or [],
            "skill_dependencies": self.skill_dependencies or [],
            "dir_path": self.dir_path,
            "version": self.version,
            "content_hash": self.content_hash,
            "share_config": self.share_config or {},
            "enabled": bool(self.enabled),
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ── MCP Server ─────────────────────────────────────────────
class MCPServer(Base):
    __tablename__ = "mcp_servers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(100), nullable=False, unique=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(String(500), nullable=True)
    transport = Column(String(20), nullable=False)
    url = Column(String(500), nullable=True)
    command = Column(String(500), nullable=True)
    args = Column(JSON, nullable=True)
    env = Column(JSON, nullable=True)
    headers = Column(JSON, nullable=True)
    timeout = Column(Integer, nullable=True)
    sse_read_timeout = Column(Integer, nullable=True)
    tags = Column(JSON, nullable=True)
    icon = Column(String(50), nullable=True)
    enabled = Column(Integer, nullable=False, default=1)
    disabled_tools = Column(JSON, nullable=True)
    created_by = Column(String(100), nullable=False)
    updated_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "transport": self.transport,
            "url": self.url,
            "command": self.command,
            "args": self.args or [],
            "env": self.env or {},
            "headers": self.headers or {},
            "timeout": self.timeout,
            "sse_read_timeout": self.sse_read_timeout,
            "tags": self.tags or [],
            "icon": self.icon,
            "enabled": bool(self.enabled),
            "disabled_tools": self.disabled_tools or [],
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_mcp_config(self) -> dict:
        config = {"transport": self.transport}
        if self.transport in ("sse", "streamable_http") and self.url:
            config["url"] = self.url
        if self.transport == "stdio":
            if self.command:
                config["command"] = self.command
            if self.args:
                config["args"] = self.args
            if self.env:
                config["env"] = self.env
        if self.transport in ("sse", "streamable_http") and self.headers:
            config["headers"] = self.headers
        if self.timeout is not None:
            config["timeout"] = self.timeout
        if self.sse_read_timeout is not None:
            config["sse_read_timeout"] = self.sse_read_timeout
        if self.disabled_tools:
            config["disabled_tools"] = self.disabled_tools
        return config


# ── Model Provider ─────────────────────────────────────────
class ModelProvider(Base):
    __tablename__ = "model_providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(String(100), nullable=False, unique=True, index=True)
    display_name = Column(String(100), nullable=False)
    provider_type = Column(String(32), nullable=False, default="openai")
    default_protocol = Column(String(64), nullable=True)
    base_url = Column(String(500), nullable=False)
    embedding_base_url = Column(String(500), nullable=True)
    rerank_base_url = Column(String(500), nullable=True)
    models_endpoint = Column(String(200), nullable=True)
    embedding_models_endpoint = Column(String(200), nullable=True)
    rerank_models_endpoint = Column(String(200), nullable=True)
    api_key_env = Column(String(128), nullable=True)
    api_key = Column(String(500), nullable=True)
    capabilities = Column(JSON, nullable=False, default=list)
    enabled_models = Column(JSON, nullable=False, default=list)
    headers_json = Column(JSON, nullable=True)
    extra_json = Column(JSON, nullable=True)
    is_enabled = Column(Boolean, nullable=False, default=True, index=True)
    is_builtin = Column(Boolean, nullable=False, default=False)
    created_by = Column(String(100), nullable=True)
    updated_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "provider_type": self.provider_type,
            "default_protocol": self.default_protocol,
            "base_url": self.base_url,
            "embedding_base_url": self.embedding_base_url,
            "rerank_base_url": self.rerank_base_url,
            "models_endpoint": self.models_endpoint,
            "embedding_models_endpoint": self.embedding_models_endpoint,
            "rerank_models_endpoint": self.rerank_models_endpoint,
            "api_key_env": self.api_key_env,
            "api_key": self.api_key,
            "capabilities": self.capabilities or [],
            "enabled_models": self.enabled_models or [],
            "headers_json": self.headers_json or {},
            "extra_json": self.extra_json or {},
            "is_enabled": bool(self.is_enabled),
            "is_builtin": bool(self.is_builtin),
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ── Task ───────────────────────────────────────────────────
class TaskRecord(Base):
    __tablename__ = "tasks"

    id = Column(String(32), primary_key=True)
    name = Column(String(255), nullable=False)
    type = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    progress = Column(Float, nullable=False, default=0.0)
    message = Column(Text, nullable=False, default="")
    payload = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    cancel_requested = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utc_now_naive, index=True)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "payload": self.payload or {},
            "result": self.result,
            "error": self.error,
            "cancel_requested": bool(self.cancel_requested),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    def to_summary_dict(self) -> dict:
        data = self.to_dict()
        data.pop("payload", None)
        data.pop("result", None)
        return data


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"
    __table_args__ = (Index("ix_scheduled_tasks_uid_enabled", "uid", "enabled"),)

    id = Column(String(64), primary_key=True)
    uid = Column(String(64), ForeignKey("users.uid", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    cron = Column(String(128), nullable=False)
    prompt = Column(Text, nullable=False)
    agent_slug = Column(String(128), nullable=False, default="default-chatbot", server_default="default-chatbot")
    project_id = Column(String(64), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    last_task_id = Column(String(64), nullable=True)
    last_run_at = Column(DateTime, nullable=True)
    last_status = Column(String(32), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "cron": self.cron, "prompt": self.prompt,
            "agent_slug": self.agent_slug, "project_id": self.project_id, "enabled": bool(self.enabled),
            "last_task_id": self.last_task_id, "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_status": self.last_status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ── Operation Log ──────────────────────────────────────────
class OperationLog(Base):
    __tablename__ = "operation_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    operation = Column(String(255), nullable=False)
    details = Column(Text, nullable=True)
    ip_address = Column(String(64), nullable=True)
    timestamp = Column(DateTime, default=utc_now_naive, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "operation": self.operation,
            "details": self.details,
            "ip_address": self.ip_address,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


# ── AgentRun ──────────────────────────────────────────────
AGENT_RUN_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})
AGENT_RUN_ACTIVE_STATUSES = frozenset({"pending", "running", "cancel_requested", "interrupted"})

RUN_SOURCE_WEB = "web"
RUN_SOURCE_API = "api_key"
RUN_SOURCE_CHANNEL = "channel"


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        # thread_id 列已有 index=True（自动 ix_agent_runs_thread_id），勿重复声明
        Index("ix_agent_runs_uid_agent_slug_status", "uid", "agent_slug", "status"),
    )

    id = Column(String(64), primary_key=True)
    thread_id = Column(String(64), nullable=False, index=True)
    uid = Column(String(64), nullable=False, index=True)
    agent_slug = Column(String(128), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    source = Column(String(32), nullable=False, default=RUN_SOURCE_WEB)
    channel = Column(String(32), nullable=False, default="web")
    request_id = Column(String(64), unique=True, nullable=False)
    input_payload = Column(JSON, nullable=False, default=dict)
    output_message_id = Column(Integer, nullable=True)
    token_usage = Column(JSON, nullable=False, default=dict)
    error_type = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "uid": self.uid,
            "agent_slug": self.agent_slug,
            "status": self.status,
            "source": self.source,
            "channel": self.channel,
            "request_id": self.request_id,
            "input_payload": self.input_payload or {},
            "token_usage": self.token_usage or {},
            "error_type": self.error_type,
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ── RunEvent（SSE 事件持久化） ─────────────────────────────
class RunEvent(Base):
    __tablename__ = "run_events"

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), nullable=False, index=True)
    # PG identity 自增（bigserial 语义）；ORM 不传值，由 DB 生成
    seq = Column(BigInteger, Identity(always=True), nullable=False, unique=True)
    event_type = Column(String(64), nullable=False, index=True)
    payload = Column(JSON, nullable=False, default=dict)
    thread_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)

    def to_envelope(self) -> dict:
        return {
            "run_id": self.run_id,
            "thread_id": self.thread_id or "",
            "event_type": self.event_type,
            "payload": self.payload or {},
            "seq": str(self.seq),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ── MessageFeedback ──────────────────────────────────────
class MessageFeedback(Base):
    __tablename__ = "message_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), nullable=False, index=True)
    message_id = Column(String(64), nullable=False, index=True)
    rating = Column(String(16), nullable=False)  # like / dislike
    reason = Column(Text, nullable=True)
    uid = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "message_id": self.message_id,
            "rating": self.rating,
            "reason": self.reason,
            "uid": self.uid,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ── ApiKey ────────────────────────────────────────────────
class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    uid = Column(String(64), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    key_hash = Column(String(64), nullable=False, unique=True, index=True)
    key_prefix = Column(String(16), nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    last_used_at = Column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "uid": self.uid,
            "name": self.name,
            "key_prefix": self.key_prefix,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
        }


# ── Agent（智能体定义） ────────────────────────────────────
class Agent(Base):
    __tablename__ = "agents"

    id = Column(String(64), primary_key=True)
    slug = Column(String(128), nullable=False, unique=True, index=True)
    name = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    backend_id = Column(String(128), nullable=False, default="ChatbotAgent")
    config_json = Column(JSON, nullable=False, default=dict)
    is_builtin = Column(Boolean, nullable=False, default=False)
    icon = Column(String(512), nullable=True)
    share_config = Column(JSON, nullable=False, default=dict)
    is_subagent = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False)

    def to_dict(self, include_configurable_items: bool = False) -> dict:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "backend_id": self.backend_id,
            "config_json": self.config_json or {},
            "is_builtin": self.is_builtin,
            "icon": self.icon,
            "share_config": self.share_config or {},
            "is_subagent": self.is_subagent,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SystemConfig(Base):
    __tablename__ = "system_configs"
    key = Column(String(128), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False)


class UserConfig(Base):
    __tablename__ = "user_configs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    uid = Column(String(64), nullable=False, index=True, unique=True)
    config_json = Column(Text, nullable=False, default="{}")
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False)


# 扩展模型仍由功能模块实现行为，但统一从此处注册/导出，避免应用入口维护隐式模型清单。
# 这些导入放在基础模型声明之后，避免扩展模块反向导入 Base 时形成循环。
from server.services.attachment_service import ThreadAttachment  # noqa: E402,F401
from server.services.eval_service import EvaluationCase, EvaluationRun  # noqa: E402,F401
from server.services.metric_registry import MetricRegistry  # noqa: E402,F401
from server.services.pg_memory_store import AgentMemory  # noqa: E402,F401
