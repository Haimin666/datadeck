"""工具审批 interrupt/resume 全链路测试（M1 关键路径）。

链路：假模型发 tool_call → HITL interrupt → run=interrupted +
human_approval_required 事件（前端 approvalState 契约）→
POST /api/agent/runs {resume, tool_approval} → Command(resume={"decisions"}) →
工具执行 → completed。

审批谓词生产配置拦 write_file/edit_file/execute；测试图工具是 echo，
故 monkeypatch 中间件工厂把 interrupt_on 指向 echo（验证链路，不改生产配置）。
"""
from __future__ import annotations

import time

import pytest
from langchain.agents.middleware import HumanInTheLoopMiddleware

import datadeck.agents.buildin.chatbot.graph as graph_mod


@pytest.fixture
def approval_on_echo(app_client, monkeypatch):
    """HITL 拦 echo 工具（生产拦 write_file/edit_file/execute，测试图无这些工具）。"""
    def _fake_create(mode, *, current_project_path=None):
        if mode == "always_trust":
            return None
        return HumanInTheLoopMiddleware(interrupt_on={
            "echo": {"allowed_decisions": ["approve", "reject"]},
        })

    monkeypatch.setattr(graph_mod, "create_tool_approval_middleware", _fake_create)


def _wait_status(client, run_id: str, headers: dict, targets, timeout_s=10) -> dict:
    deadline = time.time() + timeout_s
    run = {}
    while time.time() < deadline:
        r = client.get(f"/api/agent/runs/{run_id}", headers=headers)
        run = r.json()["run"]
        if run["status"] in targets:
            return run
        time.sleep(0.3)
    raise TimeoutError(f"等待 run 状态超时: {run_id} -> {targets}, 当前 {run.get('status')}")


def _login(client):
    res = client.post("/api/auth/token", data={"username": "admin", "password": "admin123456"})
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _db_events(run_id: str, event_type: str) -> list[dict]:
    """直查 run_events（SSE 传输层由 test_sse_contract 覆盖；此处验证事件形状）。"""
    import psycopg
    with psycopg.connect("postgresql://localhost:5432/datadeck_test",
                         autocommit=True) as conn:
        rows = conn.execute(
            "SELECT payload FROM run_events WHERE run_id=%s AND event_type=%s "
            "ORDER BY seq", (run_id, event_type)).fetchall()
    import json
    return [r[0] if isinstance(r[0], dict) else json.loads(r[0]) for r in rows]


class TestApprovalInterruptResume:
    def test_full_approval_flow(self, app_client, approval_on_echo, scripted_model):
        # 假模型：第 1 轮调 echo；resume 后第 2 轮收尾回答
        scripted_model.reset(script=[
            {"content": "", "tool_calls": [
                {"name": "echo", "args": {"text": "危险操作"}, "id": "call_x", "type": "tool_call"},
            ]},
            {"content": "工具执行完毕。"},
        ])

        headers = _login(app_client)
        th = app_client.post("/api/chat/thread",
                             json={"agent_id": "default-chatbot", "title": "审批测试"},
                             headers=headers).json()["id"]
        run = app_client.post("/api/agent/runs",
                              json={"query": "请调用 echo", "agent_slug": "default-chatbot",
                                    "thread_id": th},
                              headers=headers).json()["run"]

        # 1. interrupt：run → interrupted + human_approval_required 事件落库
        final = _wait_status(app_client, run["id"], headers, {"interrupted"})
        assert final["status"] == "interrupted"

        approvals = _db_events(run["id"], "interrupt")
        assert approvals, "应有 interrupt(human_approval) 事件"
        chunk = approvals[-1]["chunk"]
        # 前端契约（useApproval.extractToolApprovalPayload: chunk.approval.{action_requests,review_configs}）
        assert chunk["status"] == "human_approval_required"
        assert chunk["approval"]["action_requests"][0]["name"] == "echo"
        assert chunk["approval"]["action_requests"][0]["args"] == {"text": "危险操作"}
        assert len(chunk["approval"]["review_configs"]) == len(chunk["approval"]["action_requests"])
        assert chunk["tool_names"] == ["echo"]

        # 2. resume approve：同 run 恢复执行 → completed
        resume_res = app_client.post("/api/agent/runs",
                                    json={"query": "", "agent_slug": "default-chatbot",
                                          "thread_id": th, "resume": run["id"],
                                          "tool_approval": {"approved": True}},
                                    headers=headers)
        assert resume_res.status_code == 200, resume_res.text
        assert resume_res.json()["run"]["id"] == run["id"], "resume 应复用原 run"

        final2 = _wait_status(app_client, run["id"], headers, {"completed"})
        assert final2["status"] == "completed"

    def test_reject_resume_completes(self, app_client, approval_on_echo, scripted_model):
        scripted_model.reset(script=[
            {"content": "", "tool_calls": [
                {"name": "echo", "args": {"text": "x"}, "id": "call_y", "type": "tool_call"},
            ]},
            {"content": "好的，已取消操作。"},
        ])

        headers = _login(app_client)
        th = app_client.post("/api/chat/thread",
                             json={"agent_id": "default-chatbot", "title": "拒绝测试"},
                             headers=headers).json()["id"]
        run = app_client.post("/api/agent/runs",
                              json={"query": "调用 echo", "agent_slug": "default-chatbot",
                                    "thread_id": th},
                              headers=headers).json()["run"]
        _wait_status(app_client, run["id"], headers, {"interrupted"})

        resume_res = app_client.post("/api/agent/runs",
                                    json={"query": "", "agent_slug": "default-chatbot",
                                          "thread_id": th, "resume": run["id"],
                                          "tool_approval": {"approved": False,
                                                            "reason": "不需要"}},
                                    headers=headers)
        assert resume_res.status_code == 200
        final = _wait_status(app_client, run["id"], headers, {"completed"})
        assert final["status"] == "completed"

    def test_resume_unknown_run_404(self, app_client):
        headers = _login(app_client)
        th = app_client.post("/api/chat/thread",
                             json={"agent_id": "default-chatbot", "title": "t"},
                             headers=headers).json()["id"]
        res = app_client.post("/api/agent/runs",
                              json={"query": "", "agent_slug": "default-chatbot",
                                    "thread_id": th, "resume": "not-a-run",
                                    "tool_approval": {"approved": True}},
                              headers=headers)
        assert res.status_code == 404

    def test_cancel_terminal_run_conflict(self, app_client, scripted_model):
        scripted_model.reset(script=[{"content": "好的。"}])

        headers = _login(app_client)
        th = app_client.post("/api/chat/thread",
                             json={"agent_id": "default-chatbot", "title": "t"},
                             headers=headers).json()["id"]
        run = app_client.post("/api/agent/runs",
                              json={"query": "hi", "agent_slug": "default-chatbot",
                                    "thread_id": th},
                              headers=headers).json()["run"]
        _wait_status(app_client, run["id"], headers, {"completed"})
        res = app_client.post(f"/api/agent/runs/{run['id']}/cancel", headers=headers)
        assert res.status_code == 409
