"""附件系统（P1）：Attachment 表 + 本地文件存储 + 解析。

契约对齐前端 AttachmentTmpUploadModal / AgentChatComponent：
- uploadTmpAttachment → {file_name, file_type, file_size, object_name, parse_supported, parse_methods}
- parseTmpAttachment {object_name, parse_method} → {parsed_object_name}
- confirm {attachments: [{file_type, object_name, parsed_object_name}]} → 列表
- getThreadAttachments → {attachments: [...]}
- artifacts/{path} 下载/预览（本地根目录内，防路径穿越）
"""

from __future__ import annotations

import os
import shutil
import uuid

from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from server.models import Base
from server.utils.datetime_utils import utc_now_naive

# 存储根：项目下 uploads/threads/<thread_id>/<file>
STORAGE_ROOT = os.path.realpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "uploads", "threads"))

TEXT_EXTS = {".txt", ".md", ".csv", ".log", ".json", ".yaml", ".yml", ".xml", ".html", ".py", ".js", ".sql"}
PARSE_METHODS = ["plain_text", "pdf_text"]


class ThreadAttachment(Base):
    __tablename__ = "thread_attachments"
    __table_args__ = (Index("ix_thread_attachments_thread", "thread_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(String(64), nullable=False)
    uid = Column(String(64), nullable=False)
    file_name = Column(String(256), nullable=False)
    file_type = Column(String(64), nullable=False, default="")  # mime
    file_size = Column(Integer, nullable=False, default=0)
    object_name = Column(String(128), nullable=False, unique=True)  # 原始对象名
    parsed_object_name = Column(String(128), nullable=True)         # 解析产物对象名
    parse_method = Column(String(32), nullable=True)
    status = Column(String(32), nullable=False, default="confirmed")  # tmp/parsed/confirmed
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "file_id": str(self.id),
            "thread_id": self.thread_id,
            "file_name": self.file_name,
            "file_type": self.file_type,
            "file_size": self.file_size,
            "object_name": self.object_name,
            "parsed_object_name": self.parsed_object_name,
            "parse_method": self.parse_method,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def _safe_join(thread_id: str, object_name: str) -> str:
    """thread 目录 + 对象名 → 本地路径（拒绝路径穿越）。"""
    base = os.path.join(STORAGE_ROOT, thread_id)
    path = os.path.realpath(os.path.join(base, object_name))
    if not path.startswith(os.path.realpath(base) + os.sep) and path != os.path.realpath(base):
        raise ValueError("非法路径")
    return path


def store_tmp_upload(uid: str, file_name: str, file_type: str, data: bytes) -> dict:
    """tmp 上传：落盘 uploads/tmp/<uid>/，返回对象契约。"""
    ext = os.path.splitext(file_name)[1].lower()
    object_name = f"{uuid.uuid4().hex}{ext}"
    parse_supported = ext in TEXT_EXTS or ext == ".pdf"
    tmp_dir = os.path.join(STORAGE_ROOT, "_tmp", uid)
    os.makedirs(tmp_dir, exist_ok=True)
    with open(os.path.join(tmp_dir, object_name), "wb") as f:
        f.write(data)
    return {
        "file_name": file_name,
        "file_type": file_type or "",
        "file_size": len(data),
        "object_name": object_name,
        "parse_supported": parse_supported,
        "parse_methods": PARSE_METHODS if parse_supported else [],
    }


def parse_tmp_file(uid: str, object_name: str, parse_method: str) -> dict:
    """解析 tmp 文件 → 产出 parsed_<name>.txt（同目录），返回 parsed_object_name。"""
    tmp_dir = os.path.join(STORAGE_ROOT, "_tmp", uid)
    src = _safe_join("_tmp", f"{uid}/{object_name}") if False else os.path.join(tmp_dir, object_name)
    if not os.path.isfile(src):
        raise ValueError("临时文件不存在或已过期")
    parsed_name = f"parsed_{uuid.uuid4().hex}.txt"
    text = _extract_text(src, parse_method)
    with open(os.path.join(tmp_dir, parsed_name), "w", encoding="utf-8") as f:
        f.write(text)
    return {"parsed_object_name": parsed_name, "parsed_chars": len(text)}


def _extract_text(path: str, parse_method: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf" or parse_method == "pdf_text":
        try:
            from pypdf import PdfReader

            reader = PdfReader(path)
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        except ImportError:
            raise ValueError("PDF 解析暂不可用（服务器未安装 pypdf），请上传文本类文件")
    # 默认按 UTF-8 文本读（gbk 兜底）
    for enc in ("utf-8", "gbk"):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    raise ValueError("文件编码无法识别，请转为 UTF-8 文本后上传")


def confirm_attachments(
    db, uid: str, thread_id: str, attachments: list[dict],
) -> tuple[list[ThreadAttachment], list[dict]]:
    """把 tmp 对象转正：从 _tmp/<uid>/ 移入 threads/<thread_id>/ 并落库。

    返回 (ORM 对象列表, to_dict 延迟构建器占位)；id/created_at 由调用方
    commit+refresh 后经 export_attachments() 生成——PG 整型自增主键在
    commit 后才保证回填。
    """
    dest_dir = os.path.join(STORAGE_ROOT, thread_id)
    os.makedirs(dest_dir, exist_ok=True)
    orm_objs: list[ThreadAttachment] = []
    for a in attachments or []:
        obj = str(a.get("object_name") or "")
        if not obj:
            continue
        src = os.path.join(STORAGE_ROOT, "_tmp", uid, obj)
        parsed = str(a.get("parsed_object_name") or "")
        parsed_src = (
            os.path.join(STORAGE_ROOT, "_tmp", uid, parsed) if parsed else None)
        if not os.path.isfile(src):
            continue  # 跳过失效 tmp（不中断整批）
        att = ThreadAttachment(
            thread_id=thread_id, uid=uid,
            file_name=str(a.get("file_name") or obj),
            file_type=str(a.get("file_type") or ""),
            file_size=os.path.getsize(src),
            object_name=obj,
            parsed_object_name=parsed or None,
            parse_method="pdf_text" if parsed else None,
            status="confirmed",
        )
        db.add(att)
        orm_objs.append(att)
        shutil.move(src, os.path.join(dest_dir, obj))
        if parsed_src and os.path.isfile(parsed_src):
            shutil.move(parsed_src, os.path.join(dest_dir, parsed))
    return orm_objs, []


def export_attachments(orm_objs: list[ThreadAttachment]) -> list[dict]:
    """commit/refresh 后导出（保证 id/created_at 就绪）。"""
    return [a.to_dict() for a in orm_objs]


def read_artifact(thread_id: str, path: str, download: bool) -> tuple[bytes, str, str] | None:
    """读制品：(bytes, file_name, media_type)；路径穿越/不存在返回 None。"""
    full = os.path.realpath(os.path.join(STORAGE_ROOT, thread_id, *path.split("/")))
    base = os.path.realpath(os.path.join(STORAGE_ROOT, thread_id))
    if not full.startswith(base + os.sep):
        raise ValueError("非法路径")  # 穿越 → 403
    if not os.path.isfile(full):
        return None  # 不存在 → 404
    with open(full, "rb") as f:
        data = f.read()
    file_name = os.path.basename(full)
    import mimetypes

    media = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    return data, file_name, (media if download else media)
