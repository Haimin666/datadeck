"""评测路由（阶段四 4.2）：评测集 CRUD + 批量跑分。"""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_required_user
from server.models import Base  # noqa: F401  (ensure metadata)
from server.services.eval_service import EvaluationCase, EvaluationRun, judge_case

eval_router = APIRouter(prefix="/eval", tags=["eval"])


class CaseIn(BaseModel):
    dataset: str = Field(default="default", max_length=64)
    question: str = Field(min_length=1, max_length=2048)
    expect_class: str = Field(pattern="^(metric|schema|data|chat)$")
    expect_tools: list[str] = Field(default_factory=list)
    expect_keywords: list[str] = Field(default_factory=list)


def _admin_check(user) -> None:
    if getattr(user, "role", "") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")


@eval_router.post("/cases")
async def add_case(payload: CaseIn, current_user=Depends(get_required_user),
                   db: AsyncSession = Depends(get_db)):
    _admin_check(current_user)
    case = EvaluationCase(**payload.model_dump())
    db.add(case)
    await db.commit()
    return {"ok": True, "id": case.id}


@eval_router.get("/cases")
async def list_cases(dataset: str = "default", current_user=Depends(get_required_user),
                     db: AsyncSession = Depends(get_db)):
    _admin_check(current_user)
    r = await db.execute(sa_select(EvaluationCase).where(EvaluationCase.dataset == dataset))
    return {"cases": [
        {"id": c.id, "question": c.question, "expect_class": c.expect_class,
         "expect_tools": c.expect_tools, "expect_keywords": c.expect_keywords}
        for c in r.scalars()
    ]}


@eval_router.post("/runs")
async def run_evaluation(payload: dict, current_user=Depends(get_required_user),
                         db: AsyncSession = Depends(get_db)):
    """批量跑分：dataset 内全部 case 走真实链路，统计准确率。

    payload: {"dataset": "default", "limit": 10, "token": "<登录JWT，内部回环调用用>"}
    """
    _admin_check(current_user)
    dataset = payload.get("dataset", "default")
    limit = int(payload.get("limit", 10))
    token = str(payload.get("token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="需要提供登录 token（data: {\"token\": \"<JWT>\"}）供评测内部调用")

    r = await db.execute(sa_select(EvaluationCase).where(EvaluationCase.dataset == dataset).limit(limit))
    cases = list(r.scalars())
    if not cases:
        raise HTTPException(status_code=404, detail=f"评测集 {dataset} 为空")

    results = []
    for case in cases:
        answer, tools, status = await _run_one(token, case.question)
        verdict = judge_case(answer, tools, case.expect_tools or [], case.expect_keywords or [])
        results.append({
            "question": case.question,
            "expect_class": case.expect_class,
            "status": status,
            "called_tools": tools,
            **verdict,
        })

    total = len(results)
    passed = sum(1 for x in results if x["passed"])
    tool_acc = round(100 * sum(1 for x in results if x["tool_hit"]) / total)
    faith = round(100 * sum(1 for x in results if x["keyword_hit"]) / total)

    run = EvaluationRun(
        id=str(uuid.uuid4()), dataset=dataset, total=total, passed=passed,
        tool_accuracy=tool_acc, faithfulness=faith, details=results,
    )
    db.add(run)
    await db.commit()
    return {
        "ok": True, "dataset": dataset, "total": total, "passed": passed,
        "tool_accuracy": tool_acc, "faithfulness": faith, "details": results,
    }


async def _run_one(token: str, question: str) -> tuple[str, list[str], str]:
    """单 case 走真实 run + SSE 链路。"""
    base = "http://localhost:8000"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=base, timeout=httpx.Timeout(240)) as c:
        th = (await c.post("/api/chat/thread", json={"agent_id": "default-chatbot", "title": "eval"},
                           headers=headers)).json()["thread"]["id"]
        r = await c.post("/api/agent/runs", json={
            "query": question, "agent_slug": "default-chatbot",
            "thread_id": th, "queue_policy": "enqueue"}, headers=headers)
        rid = r.json()["run"]["id"]

        answer, tools = "", []
        async with c.stream("GET", f"/api/agent/runs/{rid}/events", headers=headers) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:])
                except json.JSONDecodeError:
                    continue
                if ev.get("event") == "stream_event":
                    p = ev["payload"]
                    if p.get("type") == "message_delta":
                        answer += p["delta"]["content"]
                    elif p.get("type") == "tool_call":
                        tools.append(p["tool_call"]["name"])
        status = (await c.get(f"/api/agent/runs/{rid}", headers=headers)).json()["run"]["status"]
        return answer, tools, status
