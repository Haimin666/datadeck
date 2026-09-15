"""临时会话 Workdir：仅供单次 Agent 会话使用。"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from server.config import settings
from server.workspace.paths import workspace_uid_dirname


def _session_root() -> Path:
    root = Path(settings.temp_session_root).expanduser()
    if not root.is_absolute() or root == Path("/"):
        raise ValueError("temp_session_root 必须是绝对路径且不能是根目录")
    return root


def _safe_component(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in normalized):
        raise ValueError("临时会话标识非法")
    return normalized


def _safe_scope_path(path: str | None) -> Path:
    raw = str(path or "/").strip() or "/"
    pure = PurePosixPath(raw)
    if not pure.is_absolute() or ".." in pure.parts or "\\" in raw or "://" in raw:
        raise ValueError("invalid temporary Workdir path")
    return Path(*pure.parts[1:]) if len(pure.parts) > 1 else Path()


@dataclass(frozen=True, slots=True)
class TemporaryWorkdir:
    """把 `/tmp` 下一个会话目录包装为受限 Workdir capability。"""

    relative_path: str
    host_root: Path

    def __post_init__(self) -> None:
        # macOS 的 /tmp、测试临时目录等可能经过符号链接映射到
        # /private/var；读写和相对路径计算必须使用同一个规范化根。
        object.__setattr__(self, "host_root", self.host_root.expanduser().resolve())

    @property
    def root_path(self) -> str:
        return "/"

    def _resolve(self, path: str | None) -> Path:
        relative = _safe_scope_path(path)
        root = self.host_root
        raw_target = self.host_root / relative
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("临时 Workdir 不允许符号链接")
        target = raw_target.resolve()
        if target != root and root not in target.parents:
            raise ValueError("path is outside the temporary Workdir")
        os.utime(root, None)
        return target

    def list_directory(self, path: str = "/") -> list[dict]:
        directory = self._resolve(path)
        if not directory.is_dir():
            raise ValueError("目录不存在")
        result = []
        for item in sorted(directory.iterdir(), key=lambda entry: entry.name.lower()):
            if item.is_symlink() or not (item.is_file() or item.is_dir()):
                continue
            stat = item.stat()
            result.append({"name": item.name, "is_dir": item.is_dir(),
                           "size": stat.st_size, "mtime": stat.st_mtime})
        return result

    def search(self, query: str, **limits) -> list[dict]:
        needle = str(query or "").lower()
        max_results = min(max(int(limits.get("max_results", 50)), 1), 500)
        result = []
        for base, dirs, files in os.walk(self.host_root, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(base) / name).is_symlink()]
            for name in [*dirs, *files]:
                path = Path(base) / name
                if needle not in name.lower() and needle not in str(path.relative_to(self.host_root)).lower():
                    continue
                stat = path.stat()
                result.append({"path": f"/{path.relative_to(self.host_root).as_posix()}",
                               "name": name, "is_dir": path.is_dir(),
                               "size": stat.st_size, "mtime": stat.st_mtime})
                if len(result) >= max_results:
                    return result
        return result

    def read_file_prefix(self, path: str, max_bytes: int) -> tuple[bytes, bool]:
        target = self._resolve(path)
        if not target.is_file() or target.is_symlink():
            raise ValueError("文件不存在")
        with target.open("rb") as handle:
            data = handle.read(max_bytes + 1)
        return data[:max_bytes], len(data) > max_bytes

    def write_file(self, path: str, content: bytes) -> dict:
        target = self._resolve(path)
        if target == self.host_root or target.is_dir():
            raise ValueError("目标不是文件")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return {"path": f"/{target.relative_to(self.host_root).as_posix()}", "size": len(content)}


def open_temporary_workdir(uid: str, thread_id: str) -> TemporaryWorkdir:
    user_component = workspace_uid_dirname(uid)
    session_component = _safe_component(thread_id)
    root = _session_root() / user_component / session_component
    root.mkdir(parents=True, exist_ok=True)
    now = time.time()
    os.utime(root, (now, now))
    return TemporaryWorkdir(
        relative_path=f"tmp/{user_component}/{session_component}",
        host_root=root.resolve(),
    )


def cleanup_expired_temporary_workdirs() -> int:
    """删除超过 TTL 的会话目录，只操作本服务专属根目录的两级子目录。"""
    root = _session_root()
    if not root.exists():
        return 0
    try:
        ttl_days = max(int(settings.temp_session_ttl_days), 1)
    except (TypeError, ValueError):
        ttl_days = 7
    deadline = time.time() - ttl_days * 24 * 60 * 60
    removed = 0
    for user_dir in root.iterdir():
        if user_dir.is_symlink() or not user_dir.is_dir():
            continue
        for session_dir in user_dir.iterdir():
            if session_dir.is_symlink() or not session_dir.is_dir():
                continue
            if session_dir.stat().st_mtime >= deadline:
                continue
            shutil.rmtree(session_dir)
            removed += 1
        try:
            if not any(user_dir.iterdir()):
                user_dir.rmdir()
        except OSError:
            pass
    return removed


__all__ = ["TemporaryWorkdir", "cleanup_expired_temporary_workdirs", "open_temporary_workdir"]
