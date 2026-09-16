"""附件系统（P1）：Attachment 表 + 本地文件存储。

契约对齐前端 AttachmentTmpUploadModal / AgentChatComponent：
- uploadTmpAttachment → {file_name, file_type, file_size, object_name}
- confirm {attachments: [{file_type, object_name}]} → 列表
- getThreadAttachments → {attachments: [...]}
- artifacts/{path} 下载/预览（本地根目录内，防路径穿越）
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import PurePath

from server.models import ThreadAttachment

MAX_RUNTIME_ARTIFACT_BYTES = 32 * 1024 * 1024

# 存储根：项目下 uploads/threads/<thread_id>/<file>；运行产物使用 outputs/<request_id>/
STORAGE_ROOT = os.path.realpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "uploads", "threads"))


def _safe_segment(value: object, label: str) -> str:
    """校验只能作为目录名使用的受控标识。"""
    segment = str(value or "")
    pure = PurePath(segment)
    if (
        not segment
        or segment != pure.name
        or pure.name in {".", ".."}
        or "/" in segment
        or "\\" in segment
    ):
        raise ValueError(f"非法{label}")
    return segment


def _safe_object_name(value: object) -> str:
    """校验由浏览器回传的附件对象名，只允许临时上传生成的文件名。"""
    name = str(value or "")
    pure = PurePath(name)
    if (
        not name
        or name != pure.name
        or pure.name in {".", ".."}
        or "/" in name
        or "\\" in name
    ):
        raise ValueError("非法附件对象名")
    return name


def _safe_tmp_join(uid: str, object_name: str) -> str:
    """返回用户临时目录内的对象路径。"""
    root = os.path.realpath(os.path.join(
        STORAGE_ROOT, "_tmp", _safe_segment(uid, "用户标识")))
    path = os.path.realpath(os.path.join(root, _safe_object_name(object_name)))
    if not path.startswith(root + os.sep):
        raise ValueError("非法路径")
    return path

def _safe_join(thread_id: str, object_name: str) -> str:
    """thread 目录 + 对象名 → 本地路径（拒绝路径穿越）。"""
    base = os.path.join(STORAGE_ROOT, _safe_segment(thread_id, "线程标识"))
    path = os.path.realpath(os.path.join(base, object_name))
    if not path.startswith(os.path.realpath(base) + os.sep) and path != os.path.realpath(base):
        raise ValueError("非法路径")
    return path


def store_tmp_upload(uid: str, file_name: str, file_type: str, data: bytes) -> dict:
    """tmp 上传：落盘 uploads/tmp/<uid>/，返回对象契约。"""
    ext = os.path.splitext(file_name)[1].lower()
    object_name = f"{uuid.uuid4().hex}{ext}"
    tmp_dir = os.path.join(STORAGE_ROOT, "_tmp", _safe_segment(uid, "用户标识"))
    os.makedirs(tmp_dir, exist_ok=True)
    with open(os.path.join(tmp_dir, object_name), "wb") as f:
        f.write(data)
    return {
        "file_name": file_name,
        "file_type": file_type or "",
        "file_size": len(data),
        "object_name": object_name,
    }


def read_thread_attachment(
    thread_id: str,
    object_name: str,
    *,
    max_chars: int = 120_000,
) -> str:
    """读取已确认的线程附件文本；路径和读取大小均由服务端限制。"""
    path = _safe_join(thread_id, object_name)
    if not os.path.isfile(path) or os.path.islink(path):
        raise ValueError("附件不存在或已被删除")
    try:
        with open(path, "rb") as handle:
            data = handle.read(max_chars * 4 + 1)
    except OSError as exc:
        raise ValueError("附件暂时不可读") from exc
    text = data.decode("utf-8-sig", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[附件内容已截断]"
    return text


def confirm_attachments(
    db, uid: str, thread_id: str, attachments: list[dict],
) -> tuple[list[ThreadAttachment], list[dict]]:
    """把 tmp 对象转正：从 _tmp/<uid>/ 移入 threads/<thread_id>/ 并落库。

    返回 (ORM 对象列表, to_dict 延迟构建器占位)；id/created_at 由调用方
    commit+refresh 后经 export_attachments() 生成——PG 整型自增主键在
    commit 后才保证回填。
    """
    dest_dir = _safe_join(thread_id, "")
    os.makedirs(dest_dir, exist_ok=True)
    orm_objs: list[ThreadAttachment] = []
    for a in attachments or []:
        try:
            obj = _safe_object_name(a.get("object_name"))
            src = _safe_tmp_join(uid, obj)
        except ValueError:
            continue
        if not os.path.isfile(src):
            continue  # 跳过失效 tmp（不中断整批）
        att = ThreadAttachment(
            thread_id=thread_id, uid=uid,
            file_name=str(a.get("file_name") or obj),
            file_type=str(a.get("file_type") or ""),
            file_size=os.path.getsize(src),
            object_name=obj,
            status="confirmed",
        )
        db.add(att)
        orm_objs.append(att)
        shutil.move(src, _safe_join(thread_id, obj))
    return orm_objs, []


def export_attachments(orm_objs: list[ThreadAttachment]) -> list[dict]:
    """commit/refresh 后导出（保证 id/created_at 就绪）。"""
    return [a.to_dict() for a in orm_objs]


def remove_thread_storage(thread_id: str) -> None:
    """删除线程的附件和运行制品目录。

    线程 ID 必须是存储根目录的直接子目录；即使调用方传入异常值，也不
    允许把清理范围扩大到 ``STORAGE_ROOT`` 或其父目录。
    """
    root = os.path.realpath(STORAGE_ROOT)
    thread_name = _safe_segment(thread_id, "线程标识")
    thread_dir = os.path.join(root, thread_name)
    if os.path.dirname(thread_dir) != root or os.path.islink(thread_dir):
        raise ValueError("非法线程存储路径")
    if os.path.isdir(thread_dir):
        shutil.rmtree(thread_dir)


def read_artifact(thread_id: str, path: str, download: bool) -> tuple[bytes, str, str] | None:
    """读制品：(bytes, file_name, media_type)；路径穿越/不存在返回 None。"""
    full = _safe_join(thread_id, path)
    if not os.path.isfile(full):
        return None  # 不存在 → 404
    with open(full, "rb") as f:
        data = f.read()
    file_name = os.path.basename(full)
    import mimetypes

    media = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    return data, file_name, (media if download else media)


def _runtime_artifact_files(workdir_root: str):
    """枚举工作目录内的普通文件，outputs 目录只作为路径前缀而非扫描边界。"""
    root = os.path.realpath(workdir_root)
    if not os.path.isdir(root) or os.path.islink(root):
        return
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [
            name for name in dirs
            if name != ".git" and not os.path.islink(os.path.join(base, name))
        ]
        for name in files:
            path = os.path.join(base, name)
            if os.path.islink(path):
                continue
            try:
                stat = os.stat(path, follow_symlinks=False)
            except OSError:
                continue
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            if relative.startswith("outputs/"):
                relative = relative[len("outputs/"):]
            yield path, relative, stat


def capture_runtime_artifacts_snapshot(workdir_root: str) -> dict[str, tuple[int, int]]:
    """记录一次消息开始前工作目录文件状态，用于识别本次新增或修改的文件。"""
    if not os.path.isdir(os.path.realpath(workdir_root)):
        return {}
    snapshot: dict[str, tuple[int, int]] = {}
    for _path, relative, stat in _runtime_artifact_files(workdir_root):
        snapshot[relative] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def collect_runtime_artifacts(
    thread_id: str,
    workdir_root: str,
    before: dict[str, tuple[int, int]] | None = None,
    request_id: str = "message",
) -> list[str]:
    """收集当前消息产生的文件，并按消息请求 ID 复制到线程制品目录。"""
    root = os.path.realpath(workdir_root)
    if not os.path.isdir(root) or os.path.islink(root):
        return []
    before = before or {}
    request_segment = _safe_segment(request_id, "请求标识")
    collected: list[str] = []
    for source, relative, stat in _runtime_artifact_files(workdir_root):
        signature = (stat.st_size, stat.st_mtime_ns)
        if before.get(relative) == signature or stat.st_size > MAX_RUNTIME_ARTIFACT_BYTES:
            continue
        virtual_path = f"/outputs/{request_segment}/{relative}"
        target = _safe_join(thread_id, virtual_path.lstrip("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        temporary = f"{target}.tmp-{uuid.uuid4().hex}"
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, target)
        except OSError:
            if os.path.exists(temporary):
                os.unlink(temporary)
            continue
        collected.append(virtual_path)
    return collected
