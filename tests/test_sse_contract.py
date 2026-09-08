"""SSE 事件契约测试（DEVELOPMENT.md §4 红线：假模型驱动真图，不 mock 翻译层）。

断言 Y 侧产物形状：事件信封 {"event": type, "payload": {...}}（前端
AgentChatComponent.handleSSEEvent 解构契约）、事件序列、seq 游标语义。
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

# ── 事件信封单元（parse_after_seq 游标兼容） ─────────────────────


class TestParseAfterSeq:
    def test_pure_number(self):
        from server.event_translator import parse_after_seq
        assert parse_after_seq("42") == 42

    def test_major_minor(self):
        from server.event_translator import parse_after_seq
        assert parse_after_seq("42-0") == 42

    def test_yuxi_format(self):
        from server.event_translator import parse_after_seq
        assert parse_after_seq("12345-1") == 12345

    def test_empty_and_none(self):
        from server.event_translator import parse_after_seq
        assert parse_after_seq("") == 0
        assert parse_after_seq(None) == 0
        assert parse_after_seq("0-0") == 0

    def test_garbage_falls_back_to_zero(self):
        from server.event_translator import parse_after_seq
        assert parse_after_seq("abc") == 0


class TestSseFormat:
    def test_format_sse_has_event_id(self):
        from server.utils.sse_utils import format_sse
        line = format_sse({"event": "init", "payload": {}}, event="init", event_id="5")
        assert "event: init" in line
        assert "id: 5" in line

    def test_heartbeat_is_comment(self):
        from server.utils.sse_utils import format_heartbeat
        assert format_heartbeat().startswith(":")
        assert format_heartbeat().endswith("\n\n")


# ── 信封契约（纯翻译产物，前端解构 {event, payload}） ────────────


class TestHumanApprovalPayload:
    def test_contract_shape(self):
        from server.event_translator import _human_approval_payload
        payload = _human_approval_payload({
            "action_requests": [
                {"name": "write_file", "args": {"file_path": "/tmp/x"},
                 "description": "Tool execution requires approval"},
            ],
            "review_configs": [
                {"action_name": "write_file", "allowed_decisions": ["approve", "reject"]},
            ],
        })
        # 前端 approvalState 契约（AgentChatComponent L307）：
        assert payload["tool_names"] == ["write_file"]
        assert payload["tool_calls"][0]["name"] == "write_file"
        assert payload["actionRequests"][0]["name"] == "write_file"
        assert payload["kind"] == "tool_approval"

    def test_empty_interrupt(self):
        from server.event_translator import _human_approval_payload
        payload = _human_approval_payload({})
        assert payload["tool_calls"] == []
        assert payload["tool_names"] == []


class TestExtractAgentState:
    def test_picks_structured_keys_only(self):
        from server.event_translator import _extract_agent_state
        state = _extract_agent_state({
            "messages": ["x"],
            "token_usage": {"total_tokens": 1},
            "sql_validation": {"status": "passed"},
        })
        assert state == {"token_usage": {"total_tokens": 1}, "sql_validation": {"status": "passed"}}

    def test_none_when_no_state(self):
        from server.event_translator import _extract_agent_state
        assert _extract_agent_state({"messages": ["x"]}) is None
        assert _extract_agent_state({}) is None


# ── 端到端：POST run → 后台执行 → SSE 轮询契约 ───────────────────


def _create_thread_and_run(client: TestClient, query="你好") -> dict:
    # lifespan 已建 admin（admin123456）：initialize 409 时回退 login
    token_res = client.post("/api/auth/initialize",
                            json={"username": "sseuser", "password": "sse-pass-123"})
    if token_res.status_code == 200:
        token = token_res.json()["access_token"]
    else:
        login_res = client.post(
            "/api/auth/token",
            data={"username": "admin", "password": "admin123456"},
        )
        assert login_res.status_code == 200, login_res.text
        token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    thread_res = client.post(
        "/api/chat/thread",
        json={"agent_id": "default-chatbot", "title": "测试对话"},
        headers=headers,
    )
    thread_id = thread_res.json()["thread"]["id"]

    run_res = client.post(
        "/api/agent/runs",
        json={"query": query, "agent_slug": "default-chatbot", "thread_id": thread_id},
        headers=headers,
    )
    assert run_res.status_code == 200, run_res.text
    return {"token": token, "headers": headers, "thread_id": thread_id, "run": run_res.json()["run"]}


def _drain_sse(client: TestClient, run_id: str, headers: dict, timeout=10) -> list[dict]:
    """读 SSE 直到 end 事件或超时，返回解析后的事件列表。"""
    events: list[dict] = []
    with client.stream(
        "GET", f"/api/agent/runs/{run_id}/events", headers=headers, timeout=timeout,
    ) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if not line:
                continue
            text = line.decode() if isinstance(line, bytes) else line
            text = text.strip()
            if not text or not text.startswith("data:"):
                continue
            data_text = text[5:].strip()
            try:
                ev = json.loads(data_text)
                events.append(ev)
                if ev.get("event") == "end":
                    break
            except json.JSONDecodeError:
                pass
    return events


class TestSseEndToEnd:
    def test_envelope_shape_and_sequence(self, app_client):
        ctx = _create_thread_and_run(app_client)
        events = _drain_sse(app_client, ctx["run"]["id"], ctx["headers"])

        assert events, "SSE 流应有事件"
        # 信封契约：每条 data 都有 event + payload（前端解构）
        for ev in events:
            assert "event" in ev, f"缺 event 字段: {ev}"
            assert "payload" in ev, f"缺 payload 字段: {ev}"

        types = [ev["event"] for ev in events]
        assert types[0] == "init", f"首个事件应为 init: {types[:3]}"
        assert types[-1] == "end", f"末尾事件应为 end: {types[-3:]}"
        assert "finished" in types
        stream_events = [e for e in events if e["event"] == "stream_event"]
        assert any(e["payload"].get("type") == "message_delta" for e in stream_events)
        finished = [e for e in events if e["event"] == "finished"][-1]
        assert finished["payload"]["run"]["status"] == "completed"

    def test_message_delta_payload_contract(self, app_client):
        ctx = _create_thread_and_run(app_client)
        events = _drain_sse(app_client, ctx["run"]["id"], ctx["headers"])
        deltas = [e["payload"] for e in events
                  if e["event"] == "stream_event" and e["payload"].get("type") == "message_delta"]
        assert deltas
        for d in deltas:
            # AgentChatComponent L293: const { type, delta } = payload; delta.content
            assert "delta" in d and "content" in d["delta"]
            assert d.get("message", {}).get("role") == "assistant"

    def test_run_status_transitions_to_completed(self, app_client):
        ctx = _create_thread_and_run(app_client)
        _drain_sse(app_client, ctx["run"]["id"], ctx["headers"])
        res = app_client.get(f"/api/agent/runs/{ctx['run']['id']}", headers=ctx["headers"])
        assert res.status_code == 200
        assert res.json()["run"]["status"] == "completed"

    def test_replay_after_end_replays_all(self, app_client):
        """终态 run 重连：全量事件可重放（Last-Event-ID=0）。"""
        ctx = _create_thread_and_run(app_client)
        first = _drain_sse(app_client, ctx["run"]["id"], ctx["headers"])
        assert "end" in [e["event"] for e in first]

        replay = _drain_sse(app_client, ctx["run"]["id"], ctx["headers"])
        assert replay[0]["event"] == "init"
        assert replay[-1]["event"] == "end"
