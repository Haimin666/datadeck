"""P1 路由：附件系统 + @提及 + 队列语义映射 + Tasker/Langfuse 占位。

契约对齐前端 agent_api.js threadApi/agentApi：
- POST /api/chat/attachments/tmp、/tmp/parse、/thread/{id}/attachments/confirm
- GET/DELETE /api/chat/thread/{id}/attachments[/{file_id}]
- GET /api/chat/thread/{id}/artifacts/{path...}（下载/预览）
- POST /api/chat/thread/{id}/artifacts/save（workspace 概念不存在 → 复制到 thread 目录）
- POST /api/chat/image/upload（多模态别名）
- GET /api/mention/search
- GET /api/agent/thread/{id}/requests、POST .../continue、GET /api/agent/requests/{id}、
  POST .../cancel|steer、GET .../events（队列语义映射到 run）
- GET /api/agent/runs/{id}/langfuse（占位 {url:null}）
- /api/tasks（占位空列表）
"""

from __future__ import annotations

import os
import shutil
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_required_user
from server.models import User
from server.services import attachment_service as att

p1 = APIRouter(tags=["attachments"])


async def _require_thread_owner(db: AsyncSession, thread_id: str, uid: str) -> None:
    r = await db.execute(sa_text("SELECT 1 FROM threads WHERE id=:t AND uid=:u"),
                         {"t": thread_id, "u": uid})
    if r.fetchone() is None:
        raise HTTPException(status_code=404, detail="对话不存在")


# ── 临时附件 ──────────────────────────────────────────────

@p1.post("/chat/attachments/tmp")
async def upload_tmp(
    file: UploadFile,
    current_user: User = Depends(get_required_user),
):
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="附件不能超过 50MB")
    result = att.store_tmp_upload(current_user.uid, file.filename or "file",
                                  file.content_type or "", data)
    return result


@p1.post("/chat/attachments/tmp/parse")
async def parse_tmp(
    body: dict,
    current_user: User = Depends(get_required_user),
):
    obj = str(body.get("object_name") or "")
    if not obj:
        raise HTTPException(status_code=422, detail="object_name 不能为空")
    try:
        return att.parse_tmp_file(current_user.uid, obj, str(body.get("parse_method") or "plain_text"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@p1.post("/chat/thread/{thread_id}/attachments/confirm")
async def confirm_attachments(
    thread_id: str,
    body: dict,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_thread_owner(db, thread_id, current_user.uid)
    items = body.get("attachments") or []
    orm_objs, _ = att.confirm_attachments(db, current_user.uid, thread_id, items)
    await db.commit()
    for a in orm_objs:
        await db.refresh(a)
    return {"attachments": att.export_attachments(orm_objs)}


# ── 线程附件列表/删除 ────────────────────────────────────

@p1.get("/chat/thread/{thread_id}/attachments")
async def list_attachments(
    thread_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_thread_owner(db, thread_id, current_user.uid)
    r = await db.execute(sa_text(
        "SELECT id, file_name, file_type, file_size, object_name, parsed_object_name, "
        "parse_method, status, created_at FROM thread_attachments WHERE thread_id=:t "
        "ORDER BY id DESC"), {"t": thread_id})
    return {"attachments": [
        {"id": x[0], "file_id": str(x[0]), "file_name": x[1], "file_type": x[2],
         "file_size": x[3], "object_name": x[4], "parsed_object_name": x[5],
         "parse_method": x[6], "status": x[7],
         "created_at": x[8].isoformat() if x[8] else None}
        for x in r.fetchall()
    ]}


@p1.delete("/chat/thread/{thread_id}/attachments/{file_id}")
async def delete_attachment(
    thread_id: str,
    file_id: int,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_thread_owner(db, thread_id, current_user.uid)
    r = await db.execute(sa_text(
        "SELECT object_name, parsed_object_name FROM thread_attachments "
        "WHERE thread_id=:t AND uid=:u AND id=:f"),
        {"t": thread_id, "u": current_user.uid, "f": file_id})
    row = r.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="附件不存在")
    for name in filter(None, row):
        p = os.path.join(att.STORAGE_ROOT, thread_id, name)
        if os.path.isfile(p):
            os.remove(p)
    await db.execute(sa_text(
        "DELETE FROM thread_attachments WHERE thread_id=:t AND id=:f"),
        {"t": thread_id, "f": file_id})
    await db.commit()
    return {"ok": True}


# ── 制品（artifacts）下载/预览/另存 ──────────────────────

@p1.get("/chat/thread/{thread_id}/artifacts/{file_path:path}")
async def get_artifact(
    thread_id: str,
    file_path: str,
    download: bool = Query(False),
    preview: bool = Query(False),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_thread_owner(db, thread_id, current_user.uid)
    try:
        result = att.read_artifact(thread_id, file_path, download)
    except ValueError:
        raise HTTPException(status_code=403, detail="非法路径")
    if result is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    data, file_name, media = result
    headers = {"Content-Disposition": f'attachment; filename="{file_name}"'} if download else {}
    return Response(content=data, media_type=media, headers=headers)


@p1.post("/chat/thread/{thread_id}/artifacts/save")
async def save_artifact(
    thread_id: str,
    body: dict,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """workspace 概念在 datadeck 不存在 → 复制制品到 thread 目录（保持前端可用语义）。"""
    await _require_thread_owner(db, thread_id, current_user.uid)
    src = str(body.get("path") or "")
    dest = str(body.get("destination_path") or "saved")
    try:
        data = att.read_artifact(thread_id, src, True)
    except ValueError:
        raise HTTPException(status_code=403, detail="非法路径")
    if data is None:
        raise HTTPException(status_code=404, detail="制品不存在")
    dest_dir = os.path.join(att.STORAGE_ROOT, thread_id, *dest.split("/"))
    os.makedirs(os.path.dirname(dest_dir), exist_ok=True)
    shutil.copyfile(os.path.join(att.STORAGE_ROOT, thread_id, *src.split("/")), dest_dir)
    return {"ok": True, "saved_path": dest}


# ── 聊天图片上传（多模态别名，沿用 uploads/images） ───────

@p1.post("/chat/image/upload")
async def upload_chat_image(
    file: UploadFile,
    current_user: User = Depends(get_required_user),
):
    allowed = (".png", ".jpg", ".jpeg", ".webp", ".gif")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in allowed:
        raise HTTPException(status_code=422, detail=f"仅支持 {'/'.join(allowed)}")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="图片不能超过 10MB")
    base = os.path.join(os.path.dirname(__file__), "..", "..", "uploads")
    img_dir = os.path.join(base, "images")
    os.makedirs(img_dir, exist_ok=True)
    name = f"{_uuid.uuid4().hex}{ext}"
    with open(os.path.join(img_dir, name), "wb") as f:
        f.write(data)
    return {"ok": True, "url": f"/uploads/images/{name}"}


# ── @提及搜索（thread 附件名匹配） ───────────────────────

@p1.get("/mention/search")
async def mention_search(
    thread_id: str = Query(""),
    query: str = Query(""),
    limit: int = Query(10, ge=1, le=50),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """提及候选：优先当前 thread 附件，全局兜底（按文件名 ILIKE）。"""
    q = f"%{query}%" if query else "%"
    sql = ("SELECT file_name, object_name, file_type, file_size, thread_id FROM thread_attachments "
           "WHERE uid=:u AND file_name ILIKE :q")
    params: dict = {"u": current_user.uid, "q": q, "lim": limit}
    if thread_id:
        sql += " AND thread_id=:t"
        params["t"] = thread_id
    sql += " ORDER BY id DESC LIMIT :lim"
    r = await db.execute(sa_text(sql), params)
    return {"results": [
        {"file_name": x[0], "object_name": x[1], "file_type": x[2],
         "file_size": x[3], "thread_id": x[4]}
        for x in r.fetchall()
    ]}


# ── 请求队列语义映射（Yuxi 队列 → datadeck run） ─────────

@p1.get("/agent/thread/{thread_id}/requests")
async def list_thread_requests(
    thread_id: str,
    agent_slug: str = Query(""),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """排队请求 = 该 thread 的 runs（最新在前）。"""
    r = await db.execute(sa_text(
        "SELECT id, status, agent_slug, input_payload, created_at, finished_at "
        "FROM agent_runs WHERE thread_id=:t AND uid=:u ORDER BY created_at DESC LIMIT 50"),
        {"t": thread_id, "u": current_user.uid})
    return {"requests": [
        {"id": x[0], "request_id": x[0], "status": x[1], "agent_slug": x[2],
         "query": (x[3] or {}).get("query", ""), "created_at": str(x[4]),
         "finished_at": str(x[5]) if x[5] else None}
        for x in r.fetchall()
    ]}


@p1.get("/agent/requests/{request_id}")
async def get_request(
    request_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """请求详情 = run 详情（队列无独立实体）。"""
    r = await db.execute(sa_text(
        "SELECT id, thread_id, status, agent_slug, input_payload, created_at "
        "FROM agent_runs WHERE id=:r AND uid=:u"),
        {"r": request_id, "u": current_user.uid})
    x = r.fetchone()
    if not x:
        raise HTTPException(status_code=404, detail="请求不存在")
    return {"request": {"id": x[0], "request_id": x[0], "thread_id": x[1], "status": x[2],
                        "agent_slug": x[3], "query": (x[4] or {}).get("query", ""),
                        "created_at": str(x[5])}}


@p1.post("/agent/requests/{request_id}/cancel")
async def cancel_request(
    request_id: str,
    current_user: User = Depends(get_required_user),
):
    """取消请求 = 取消对应 run（复用既有逻辑）。"""
    from server.services.run_service import request_cancel

    try:
        status = await request_cancel(request_id, current_user.uid)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"request_id": request_id, "status": status}


@p1.post("/agent/thread/{thread_id}/requests/continue")
async def continue_queue(
    thread_id: str,
    agent_slug: str = Query(""),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """datadeck 无队列阻塞 → 恒可直接继续。"""
    return {"ok": True, "queue_policy": "direct", "thread_id": thread_id}


@p1.post("/agent/requests/{request_id}/steer")
async def steer_request(request_id: str, current_user: User = Depends(get_required_user)):
    """运行中转向：datadeck 单 agent 暂不支持 → 明确 409 语义。"""
    raise HTTPException(status_code=409, detail="当前版本不支持运行中转向（steer）")


@p1.get("/agent/requests/{request_id}/events")
async def stream_request_events(
    request_id: str,
    request: Request,
    current_user: User = Depends(get_required_user),
):
    """请求事件流 = run 事件流（别名转发）。"""
    from server.event_translator import poll_run_events

    cursor_raw = request.headers.get("Last-Event-ID") or request.query_params.get("after_seq", "0-0")
    from server.event_translator import parse_after_seq

    after_seq = parse_after_seq(cursor_raw)
    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        poll_run_events(request_id, after_seq=after_seq, current_uid=current_user.uid),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


# ── Langfuse / Tasker 占位（优雅降级） ────────────────────

@p1.get("/agent/runs/{run_id}/langfuse")
async def get_langfuse_link(run_id: str, current_user: User = Depends(get_required_user)):
    """未接入 Langfuse：返回空 URL，前端隐藏追踪入口。"""
    return {"url": None, "enabled": False}


@p1.get("/tasks")
async def list_tasks(current_user: User = Depends(get_required_user)):
    return {"tasks": [], "total": 0}


@p1.get("/tasks/{task_id}")
async def get_task(task_id: str, current_user: User = Depends(get_required_user)):
    raise HTTPException(status_code=404, detail="任务不存在")


@p1.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, current_user: User = Depends(get_required_user)):
    raise HTTPException(status_code=404, detail="任务不存在")


@p1.delete("/tasks/{task_id}")
async def delete_task(task_id: str, current_user: User = Depends(get_required_user)):
    raise HTTPException(status_code=404, detail="任务不存在")
