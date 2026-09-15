"""知识库 Git 代码源：只拉取、只索引文本，不执行仓库代码。"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from urllib.parse import urlparse
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import settings
from server.models import CodeRepository, CodeRepositoryFile
from server.services.knowledge_service import get_knowledge_base
from server.utils.datetime_utils import utc_now_naive

def _repository_storage_root() -> Path:
    return (Path(os.getenv("DATADECK_DATA_ROOT", "data")) / "code-repositories").resolve()


def _safe_repository_path(local_path: str | os.PathLike[str]) -> Path:
    """限制仓库目录只能是应用数据目录的直接子目录。"""
    root = _repository_storage_root()
    path = Path(local_path).resolve()
    if path.parent != root or path == root:
        raise ValueError("代码仓库存储路径非法")
    return path


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.jwt_secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _repo_root(repo_id: str) -> Path:
    root = _safe_repository_path(_repository_storage_root() / repo_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii") if value else ""


def _git(repo: CodeRepository, args: list[str], *, key_file: str = "", access_token: str = "") -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"}
    if key_file:
        env["GIT_SSH_COMMAND"] = f"ssh -i {key_file} -o IdentitiesOnly=yes -o StrictHostKeyChecking=no"
    if access_token and repo.repo_url.startswith(("http://", "https://")):
        # 内网 GitLab 的 HTTP 拉取使用 Basic 认证（oauth2:token），Bearer
        # 会被 Git 当作未认证请求，随后触发 terminal prompt 并失败。
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraHeader"
        basic = base64.b64encode(f"oauth2:{access_token}".encode("utf-8")).decode("ascii")
        env["GIT_CONFIG_VALUE_0"] = f"Authorization: Basic {basic}"
    local_path = _safe_repository_path(repo.local_path)
    cwd = local_path.parent if args and args[0] == "clone" else local_path
    result = subprocess.run(["git", *args], cwd=cwd, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300, check=False)
    if result.returncode:
        raise RuntimeError(result.stdout[-4000:] or f"git exited with {result.returncode}")
    return result.stdout.strip()


def _clone_url(repo: CodeRepository, *, has_key: bool) -> str:
    """SSH Key 配合 HTTP(S) 地址时转换为同主机的 Git SSH 地址。"""
    parsed = urlparse(repo.repo_url)
    if has_key and parsed.scheme in {"http", "https"} and parsed.hostname and parsed.path:
        return f"git@{parsed.hostname}:{parsed.path.lstrip('/')}"
    return repo.repo_url


def _snapshot_file_metadata(root: Path, commit: str) -> list[dict]:
    """扫描当前快照并生成可持久化的文件索引，不读取二进制或超大文件。"""
    suffixes = {".sql", ".hql", ".py", ".sh", ".yaml", ".yml", ".json", ".md", ".txt", ".java", ".scala", ".js", ".ts"}
    ignored = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build"}
    result = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        relative = path.relative_to(root)
        if any(part in ignored for part in relative.parts):
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if len(raw) > 2 * 1024 * 1024:
            continue
        item = {
            "path": relative.as_posix(),
            "language": path.suffix.lower().lstrip("."),
            "size": len(raw),
            "content_hash": hashlib.sha256(raw).hexdigest(),
            "commit_sha": commit,
            "parse_status": "not_sql",
            "tables_json": [],
            "columns_json": [],
            "features_json": {"joins": [], "filters": [], "aggregations": [], "partitions": []},
        }
        if path.suffix.lower() in {".sql", ".hql"}:
            try:
                import sqlglot
                from sqlglot import exp
                statements = sqlglot.parse(raw.decode("utf-8"), read="hive")
                item["parse_status"] = "ok"
                item["tables_json"] = sorted({node.sql(dialect="hive") for statement in statements for node in statement.find_all(exp.Table)})
                item["columns_json"] = sorted({node.sql(dialect="hive") for statement in statements for node in statement.find_all(exp.Column)})
                item["features_json"] = {
                    "joins": sorted({node.sql(dialect="hive") for statement in statements for node in statement.find_all(exp.Join)}),
                    "filters": sorted({node.this.sql(dialect="hive") for statement in statements for node in statement.find_all(exp.Where)}),
                    "aggregations": sorted({node.sql(dialect="hive") for statement in statements for node in statement.find_all(exp.AggFunc)}),
                    "partitions": sorted(set(re.findall(r"\b(?:partition|dt)\s*(?:=|\(|by)?[^\n,)]*", raw.decode("utf-8"), re.I))),
                }
            except Exception as exc:  # noqa: BLE001 — 单文件解析失败不影响其他文件入索引
                item["parse_status"] = f"failed:{type(exc).__name__}"
        result.append(item)
    return result


async def _index_repository_snapshot(db: AsyncSession, repo: CodeRepository, commit: str) -> int:
    root = _safe_repository_path(repo.local_path) / (repo.subdir or "")
    root = root.resolve()
    repository_root = _safe_repository_path(repo.local_path)
    if repository_root not in root.parents and root != repository_root:
        raise ValueError("代码仓库子目录非法")
    items = await asyncio.to_thread(_snapshot_file_metadata, root, commit)
    added = 0
    for item in items:
        existing = await db.scalar(select(CodeRepositoryFile).where(
            CodeRepositoryFile.repository_id == repo.id,
            CodeRepositoryFile.commit_sha == commit,
            CodeRepositoryFile.path == item["path"],
        ))
        if existing:
            continue
        db.add(CodeRepositoryFile(id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"datadeck:code:{repo.id}:{commit}:{item['path']}")), repository_id=repo.id, **item))
        added += 1
    return added


async def list_code_repositories(db: AsyncSession, uid: str, kb_id: str) -> list[dict]:
    await get_knowledge_base(db, uid, kb_id)
    rows = (await db.execute(select(CodeRepository).where(
        CodeRepository.kb_id == kb_id, CodeRepository.uid == uid
    ).order_by(CodeRepository.updated_at.desc()))).scalars().all()
    return [item.to_dict() for item in rows]


async def create_code_repository(db: AsyncSession, uid: str, kb_id: str, data: dict) -> CodeRepository:
    await get_knowledge_base(db, uid, kb_id)
    repo_id = str(uuid.uuid4())
    item = CodeRepository(
        id=repo_id, kb_id=kb_id, uid=uid, name=str(data["name"]).strip(),
        repo_url=str(data["repo_url"]).strip(), branch=str(data.get("branch") or "main").strip(),
        subdir=str(data.get("subdir") or "").strip().strip("/"),
        ssh_key_encrypted=_encrypt(str(data.get("ssh_key") or "")),
        access_token_encrypted=_encrypt(str(data.get("access_token") or "")),
        local_path=str(_repo_root(repo_id)),
    )
    if not item.name or not item.repo_url:
        raise ValueError("仓库名称和 URL 不能为空")
    db.add(item)
    await db.flush()
    return item


async def update_code_repository(db: AsyncSession, uid: str, repo_id: str, data: dict) -> CodeRepository:
    item = await _get_repo(db, uid, repo_id)
    for key in ("name", "repo_url", "branch", "subdir"):
        if key in data and data[key] is not None:
            setattr(item, key, str(data[key]).strip().strip("/") if key in {"branch", "subdir"} else str(data[key]).strip())
    if data.get("ssh_key"):
        item.ssh_key_encrypted = _encrypt(str(data["ssh_key"]))
    if data.get("access_token"):
        item.access_token_encrypted = _encrypt(str(data["access_token"]))
    await db.flush()
    return item


async def delete_code_repository(db: AsyncSession, uid: str, repo_id: str) -> None:
    item = await _get_repo(db, uid, repo_id)
    local_path = _safe_repository_path(item.local_path)
    await db.delete(item)
    await db.flush()
    await asyncio.to_thread(shutil.rmtree, local_path, True)


async def _get_repo(db: AsyncSession, uid: str, repo_id: str) -> CodeRepository:
    item = await db.scalar(select(CodeRepository).where(CodeRepository.id == repo_id, CodeRepository.uid == uid))
    if item is None:
        raise ValueError("代码仓库不存在或无权访问")
    return item


async def pull_code_repository(db: AsyncSession, uid: str, repo_id: str) -> dict:
    repo = await _get_repo(db, uid, repo_id)
    repo.sync_status, repo.sync_error = "syncing", ""
    await db.flush()
    key_path = ""
    access_token = ""
    try:
        if repo.ssh_key_encrypted:
            with tempfile.NamedTemporaryFile(mode="w", prefix="datadeck-git-", delete=False) as key:
                key.write(_fernet().decrypt(repo.ssh_key_encrypted.encode("ascii")).decode("utf-8"))
                key.flush()
                os.chmod(key.name, 0o600)
                key_path = key.name
        if repo.access_token_encrypted:
            access_token = _fernet().decrypt(repo.access_token_encrypted.encode("ascii")).decode("utf-8")
        clone_url = _clone_url(repo, has_key=bool(key_path))
        root = _safe_repository_path(repo.local_path)
        if not (root / ".git").exists():
            root.parent.mkdir(parents=True, exist_ok=True)
            if root.exists():
                shutil.rmtree(root)
            root.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(_git, repo, ["clone", "--depth", "1", "--branch", repo.branch, clone_url, str(root)], key_file=key_path, access_token=access_token)
        else:
            await asyncio.to_thread(_git, repo, ["remote", "set-url", "origin", clone_url], key_file=key_path, access_token=access_token)
            await asyncio.to_thread(_git, repo, ["fetch", "--depth", "1", "origin", repo.branch], key_file=key_path, access_token=access_token)
            await asyncio.to_thread(_git, repo, ["reset", "--hard", f"origin/{repo.branch}"], key_file=key_path, access_token=access_token)
        commit = (await asyncio.to_thread(_git, repo, ["rev-parse", "HEAD"], key_file=key_path, access_token=access_token)).strip()
        indexed_files = await _index_repository_snapshot(db, repo, commit)
        repo.last_commit, repo.last_sync_at, repo.sync_status = commit, utc_now_naive(), "success"
        await db.flush()
        result = repo.to_dict()
        result["indexed_files"] = indexed_files
        return result
    except Exception as exc:
        repo.sync_status, repo.sync_error = "failed", str(exc)[:4000]
        await db.flush()
        raise ValueError(f"代码仓库拉取失败：{repo.sync_error}") from exc
    finally:
        if key_path:
            Path(key_path).unlink(missing_ok=True)
