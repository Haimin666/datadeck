"""系统/用户配置 KV 存储 + 工具列表（P0-8）。

- system_configs: 全局配置（管理员读写；前端 configStore 消费 {key: value} 平铺字典）
- user_configs:   每用户配置（登录即读写；enable_memory 等）
- /api/system/tools: 工具注册表列表（前端 Agent 配置下拉）
- /api/user/agent-env, /api/user/upload-image: 个人环境与头像
"""

from __future__ import annotations

import os
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import text as sa_text

from server.db import async_session_factory, get_db
from server.deps import get_required_user
from server.models import SystemConfig, User, UserConfig
from server.utils.datetime_utils import utc_now_naive

config_router = APIRouter(tags=["config"])


def _require_admin(user: User) -> None:
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")


_CONFIG_OPTION_DEFINITIONS = {
    "remote_skill_source_policy": {
        "name": "远程 Skill 来源",
        "description": "限制远程加载和安装 Skill 的域名。",
        "params": {
            "fields": [{
                "key": "allowed_hosts",
                "label": "允许的域名",
                "default": [],
                "environment": "DATADECK_SKILL_ALLOWED_HOSTS",
            }]
        },
    }
}


def _config_option(key: str, value=None) -> dict:
    definition = _CONFIG_OPTION_DEFINITIONS[key]
    return {
        "key": key,
        **definition,
        "value": value if isinstance(value, dict) else {},
        "sensitive_state": {},
    }


@config_router.get("/system/config/options")
async def get_config_options(
    current_user: User = Depends(get_required_user),
    db=Depends(get_db),
):
    """返回前端设置页使用的结构化配置项。"""
    _require_admin(current_user)
    result = await db.execute(sa_text(
        "SELECT key, value FROM system_configs WHERE key = :key"
    ), {"key": "remote_skill_source_policy"})
    values = {key: _auto_parse(value) for key, value in result.fetchall()}
    return {"options": [_config_option(key, values.get(key))
                        for key in _CONFIG_OPTION_DEFINITIONS]}


@config_router.put("/system/config/options/{key}")
async def update_config_option(
    key: str,
    body: dict,
    current_user: User = Depends(get_required_user),
    db=Depends(get_db),
):
    _require_admin(current_user)
    if key not in _CONFIG_OPTION_DEFINITIONS:
        raise HTTPException(status_code=404, detail="配置项不存在")
    value = body.get("value") or {}
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="配置项值必须是对象")
    await db.execute(sa_text("""
        INSERT INTO system_configs (key, value, updated_at) VALUES (:k, :v, :now)
        ON CONFLICT (key) DO UPDATE SET value=:v, updated_at=:now
    """), {"k": key, "v": _dump(value), "now": utc_now_naive()})
    await db.commit()
    return {"option": _config_option(key, value)}


# ── 系统配置 ──────────────────────────────────────────────

@config_router.get("/system/config")
async def get_system_config(
    current_user: User = Depends(get_required_user),
    db=Depends(get_db),
):
    _require_admin(current_user)
    r = await db.execute(sa_text("SELECT key, value FROM system_configs"))
    return {k: _auto_parse(v) for k, v in r.fetchall()}


@config_router.post("/system/config")
async def update_system_config(
    body: dict,
    current_user: User = Depends(get_required_user),
    db=Depends(get_db),
):
    _require_admin(current_user)
    key, value = body.get("key"), body.get("value")
    if not key:
        raise HTTPException(status_code=422, detail="key 不能为空")
    await db.execute(sa_text("""
        INSERT INTO system_configs (key, value, updated_at) VALUES (:k, :v, :now)
        ON CONFLICT (key) DO UPDATE SET value=:v, updated_at=:now
    """), {"k": key, "v": _dump(value), "now": utc_now_naive()})
    await db.commit()
    return {"ok": True}


@config_router.post("/system/config/update")
async def update_system_config_batch(
    body: dict,
    current_user: User = Depends(get_required_user),
    db=Depends(get_db),
):
    """批量更新：{key1: value1, ...}，返回全量配置。"""
    _require_admin(current_user)
    for key, value in (body or {}).items():
        await db.execute(sa_text("""
            INSERT INTO system_configs (key, value, updated_at) VALUES (:k, :v, :now)
            ON CONFLICT (key) DO UPDATE SET value=:v, updated_at=:now
        """), {"k": key, "v": _dump(value), "now": utc_now_naive()})
    await db.commit()
    r = await db.execute(sa_text("SELECT key, value FROM system_configs"))
    return {k: _auto_parse(v) for k, v in r.fetchall()}


@config_router.get("/system/logs")
async def get_system_logs(
    levels: str = Query(""),
    limit: int = Query(200, ge=1, le=2000),
    current_user: User = Depends(get_required_user),
):
    _require_admin(current_user)
    log_file = "/tmp/datadeck-server.log"
    entries: list[dict] = []
    if os.path.exists(log_file):
        with open(log_file, errors="ignore") as f:
            lines = f.readlines()[-limit * 3:]
        wanted = {lv.strip().upper() for lv in levels.split(",") if lv.strip()}
        for ln in lines:
            if " [ERROR] " in ln or " [WARNING] " in ln or " [INFO] " in ln:
                lvl = ("ERROR" if "[ERROR]" in ln
                       else "WARNING" if "[WARNING]" in ln else "INFO")
                if wanted and lvl not in wanted:
                    continue
                entries.append({"level": lvl, "line": ln.rstrip()[:500]})
    return {"logs": entries[-limit:]}


# ── 工具列表（前端 Agent 配置下拉） ─────────────────────

@config_router.get("/system/tools")
async def get_tools(
    category: str = Query(""),
    current_user: User = Depends(get_required_user),
):
    from datadeck.agents.toolkits.registry import get_all_extra_metadata, get_all_tool_instances

    meta = get_all_extra_metadata()
    tools = []
    for t in get_all_tool_instances():
        m = meta.get(t.name)
        if category and (m.category if m else "buildin") != category:
            continue
        tools.append({
            "name": t.name,
            "display_name": (m.display_name if m else "") or t.name,
            "description": t.description or "",
            "category": m.category if m else "buildin",
            "tags": m.tags if m else [],
        })
    return {"tools": tools}


@config_router.get("/system/tools/options")
async def get_tool_options(current_user: User = Depends(get_required_user)):
    """工具下拉选项：buildin + data 全部。"""
    from datadeck.agents.toolkits.registry import get_all_extra_metadata, get_all_tool_instances

    meta = get_all_extra_metadata()
    options = [
        {"value": t.name, "label": (meta.get(t.name).display_name if meta.get(t.name) else "") or t.name}
        for t in get_all_tool_instances()
    ]
    return {"options": options}


# ── 用户个人配置 ──────────────────────────────────────────

async def _get_user_config_json(uid: str) -> dict:
    import json

    async with async_session_factory() as db:
        r = await db.execute(sa_text(
            "SELECT config_json FROM user_configs WHERE uid=:u"), {"u": uid})
        row = r.fetchone()
    return json.loads(row[0]) if row else {}


async def _set_user_config_json(uid: str, cfg: dict) -> None:
    import json

    async with async_session_factory() as db:
        await db.execute(sa_text("""
            INSERT INTO user_configs (uid, config_json, updated_at) VALUES (:u, :c, :now)
            ON CONFLICT (uid) DO UPDATE SET config_json=:c, updated_at=:now
        """), {"u": uid, "c": json.dumps(cfg, ensure_ascii=False), "now": utc_now_naive()})
        await db.commit()


@config_router.get("/user/config")
async def get_user_config(current_user: User = Depends(get_required_user)):
    return await _get_user_config_json(current_user.uid)


@config_router.put("/user/config")
async def update_user_config(
    body: dict,
    current_user: User = Depends(get_required_user),
):
    """合并式更新（浅合并）。"""
    cfg = await _get_user_config_json(current_user.uid)
    cfg.update(body or {})
    await _set_user_config_json(current_user.uid, cfg)
    return cfg


@config_router.get("/user/agent-env")
async def get_agent_env(current_user: User = Depends(get_required_user)):
    """个人环境变量（注入 agent 上下文）。"""
    cfg = await _get_user_config_json(current_user.uid)
    return {"env": cfg.get("agent_env", {})}


@config_router.put("/user/agent-env")
async def update_agent_env(
    body: dict,
    current_user: User = Depends(get_required_user),
):
    cfg = await _get_user_config_json(current_user.uid)
    env = cfg.get("agent_env") or {}
    env.update((body or {}).get("env") or {})
    cfg["agent_env"] = env
    await _set_user_config_json(current_user.uid, cfg)
    return {"ok": True, "env": env}


@config_router.post("/user/upload-image")
async def upload_user_image(
    file: UploadFile,
    current_user: User = Depends(get_required_user),
):
    """通用图片上传（聊天图片/头像共用）。"""
    allowed = (".png", ".jpg", ".jpeg", ".webp", ".gif")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in allowed:
        raise HTTPException(status_code=422, detail=f"仅支持 {'/'.join(allowed)}")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="图片不能超过 10MB")
    # 图片使用独立目录，由 main.py 以 /uploads/images 提供静态访问。
    upload_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "uploads", "images"))
    os.makedirs(upload_dir, exist_ok=True)
    name = f"{_uuid.uuid4().hex}{ext}"
    with open(os.path.join(upload_dir, name), "wb") as f:
        f.write(data)
    return {"ok": True, "url": f"/uploads/images/{name}"}


# ── JSON 序列化小工具 ────────────────────────────────────

def _dump(value) -> str:
    import json

    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _auto_parse(raw: str):
    import json

    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw
