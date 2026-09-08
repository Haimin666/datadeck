"""datadeck 数据库模型（精简版，仅 M1 所需）。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey, Identity, Integer, String,
    Text, UniqueConstraint, Index
)
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import DeclarativeBase

from server.utils.datetime_utils import utc_now


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
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    last_login = Column(DateTime(timezone=True), nullable=True)
    is_deleted = Column(Integer, nullable=False, default=0)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "uid": self.uid,
            "role": self.role,
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
    title = Column(String(256), nullable=False, default="新的对话")
    is_pinned = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    viewed_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "uid": self.uid,
            "agent_id": self.agent_id,
            "title": self.title,
            "is_pinned": self.is_pinned,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "viewed_at": self.viewed_at.isoformat() if self.viewed_at else None,
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
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

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
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

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
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

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
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)

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
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    def to_dict(self, include_configurable_items: bool = False) -> dict:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "backend_id": self.backend_id,
            "config_json": self.config_json or {},
            "is_builtin": self.is_builtin,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
