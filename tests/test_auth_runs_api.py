"""Auth 与 threads/runs 基础 API 测试（M1）。"""
from __future__ import annotations


def _login(client):
    res = client.post("/api/auth/token", data={"username": "admin", "password": "admin123456"})
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


class TestAuth:
    def test_login_wrong_password_401(self, app_client):
        res = app_client.post("/api/auth/token",
                              data={"username": "admin", "password": "wrong"})
        assert res.status_code == 401

    def test_me_requires_auth(self, app_client):
        assert app_client.get("/api/auth/me").status_code == 401

    def test_me_with_token(self, app_client):
        headers = _login(app_client)
        res = app_client.get("/api/auth/me", headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["username"] == "admin"
        assert body["role"] == "superadmin"

    def test_check_first_run(self, app_client):
        res = app_client.get("/api/auth/check-first-run")
        assert res.status_code == 200
        assert res.json()["first_run"] is False  # lifespan 已建 admin

    def test_initialize_conflict_when_initialized(self, app_client):
        res = app_client.post("/api/auth/initialize",
                              json={"username": "x", "password": "y"})
        assert res.status_code == 409

    def test_protected_endpoints_reject_no_token(self, app_client):
        for path in ("/api/chat/threads", "/api/agent", "/api/agent/runs/x",
                     "/api/user/apikey"):
            assert app_client.get(path).status_code == 401, path

    def test_api_key_auth_uses_uid(self, app_client):
        headers = _login(app_client)
        created = app_client.post(
            "/api/user/apikey", json={"name": "test-key"}, headers=headers
        )
        assert created.status_code == 200, created.text
        api_key = created.json()["plain_key"]

        protected = app_client.get("/api/chat/threads", headers={"X-API-Key": api_key})
        assert protected.status_code == 200, protected.text

    def test_uploaded_avatar_is_served_from_canonical_uploads(self, app_client):
        headers = _login(app_client)
        image = b"test-avatar-bytes"

        uploaded = app_client.post(
            "/api/auth/upload-avatar",
            files={"file": ("avatar.png", image, "image/png")},
            headers=headers,
        )

        assert uploaded.status_code == 200, uploaded.text
        served = app_client.get(uploaded.json()["avatar"])
        assert served.status_code == 200
        assert served.content == image

    def test_custom_role_controls_module_access(self, app_client):
        admin_headers = _login(app_client)
        created_role = app_client.post(
            "/api/auth/roles",
            json={
                "slug": "knowledge-editor",
                "name": "知识库编辑",
                "permissions": ["knowledge"],
            },
            headers=admin_headers,
        )
        assert created_role.status_code == 200, created_role.text

        created_user = app_client.post(
            "/api/auth/users",
            json={"username": "kbuser", "password": "password123", "role": "knowledge-editor"},
            headers=admin_headers,
        )
        assert created_user.status_code == 200, created_user.text

        login = app_client.post("/api/auth/token", data={"username": "kbuser", "password": "password123"})
        assert login.status_code == 200, login.text
        assert login.json()["permissions"] == ["knowledge"]
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert app_client.get("/api/knowledge/databases", headers=headers).status_code == 200
        assert app_client.get("/api/metrics/registry", headers=headers).status_code == 403
        configurable = app_client.get("/api/agent/configurable-items", headers=headers)
        assert configurable.status_code == 200, configurable.text
        items = configurable.json()["configurable_items"]
        assert isinstance(items["knowledges"]["options"], list)
        assert items["skills"]["options"] == []
        assert items["subagents"]["options"] == []


class TestThreadsApi:
    def test_mention_search_returns_workspace_files_as_array(self, app_client):
        from server.workspace.paths import ensure_user_workspace, user_workspace_dir

        headers = _login(app_client)
        ensure_user_workspace("admin")
        workspace = user_workspace_dir("admin")
        (workspace / "mention-contract-check.txt").write_text("ok", encoding="utf-8")

        response = app_client.get(
            "/api/mention/search?query=mention-contract",
            headers=headers,
        )

        assert response.status_code == 200, response.text
        assert isinstance(response.json(), list)
        assert any(
            item["name"] == "mention-contract-check.txt"
            and item["source"] == "workspace"
            for item in response.json()
        )

    def test_thread_create_is_idempotent_per_request(self, app_client):
        headers = _login(app_client)
        payload = {
            "request_id": "thread-create-request-1",
            "agent_id": "default-chatbot",
            "title": "幂等创建",
        }

        first = app_client.post("/api/chat/thread", json=payload, headers=headers)
        second = app_client.post("/api/chat/thread", json=payload, headers=headers)

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["request_id"] == payload["request_id"]

    def test_thread_crud(self, app_client):
        headers = _login(app_client)
        created = app_client.post(
            "/api/chat/thread",
            json={
                "agent_id": "default-chatbot",
                "title": "新对话",
                "metadata": {"model_spec": "provider:model"},
            },
            headers=headers).json()
        assert created["title"] == "新对话"
        assert created["metadata"] == {"model_spec": "provider:model"}
        assert created["extra_metadata"] == created["metadata"]

        listed = app_client.get("/api/chat/threads", headers=headers).json()
        assert any(t["id"] == created["id"] for t in listed)

        updated = app_client.put(
            f"/api/chat/thread/{created['id']}",
            json={"title": "改名", "is_pinned": True}, headers=headers).json()
        assert updated["title"] == "改名"
        assert updated["is_pinned"] is True

        deleted = app_client.delete(f"/api/chat/thread/{created['id']}", headers=headers)
        assert deleted.status_code == 200

    def test_thread_delete_cleans_run_events_and_feedback(self, app_client, monkeypatch, tmp_path):
        """删除线程必须按真实表名清理所有运行附属数据。"""
        import uuid
        import psycopg
        from server.services import attachment_service

        monkeypatch.setattr(attachment_service, "STORAGE_ROOT", str(tmp_path))

        headers = _login(app_client)
        thread = app_client.post(
            "/api/chat/thread",
            json={"agent_id": "default-chatbot", "title": "级联删除"},
            headers=headers,
        ).json()
        run_id = str(uuid.uuid4())
        event_id = str(uuid.uuid4())
        orphan_event_id = str(uuid.uuid4())
        object_name = f"{uuid.uuid4().hex}.txt"
        thread_dir = tmp_path / thread["id"]
        thread_dir.mkdir(parents=True)
        (thread_dir / object_name).write_text("attachment", encoding="utf-8")
        with psycopg.connect("postgresql://localhost:5432/datadeck_test", autocommit=True) as conn:
            conn.execute(
                "INSERT INTO agent_runs "
                "(id, thread_id, uid, agent_slug, status, source, channel, request_id, "
                "input_payload, token_usage, created_at, updated_at) "
                "VALUES (%s, %s, 'admin', 'default-chatbot', 'completed', 'web', 'web', "
                "%s, '{}'::jsonb, '{}'::jsonb, now(), now())",
                (run_id, thread["id"], str(uuid.uuid4())),
            )
            conn.execute(
                "INSERT INTO run_events "
                "(id, run_id, event_type, payload, thread_id, created_at) "
                "VALUES (%s, %s, 'end', '{}'::jsonb, %s, now())",
                (event_id, run_id, thread["id"]),
            )
            conn.execute(
                "INSERT INTO run_events "
                "(id, run_id, event_type, payload, thread_id, created_at) "
                "VALUES (%s, %s, 'error', '{}'::jsonb, NULL, now())",
                (orphan_event_id, run_id),
            )
            conn.execute(
                "INSERT INTO message_feedback "
                "(run_id, message_id, rating, uid, created_at) "
                "VALUES (%s, 'message-1', 'like', 'admin', now())",
                (run_id,),
            )
            conn.execute(
                "INSERT INTO thread_attachments "
                "(thread_id, uid, file_name, file_type, file_size, object_name, status, created_at) "
                "VALUES (%s, 'admin', 'attachment.txt', 'text/plain', 10, %s, 'confirmed', now())",
                (thread["id"], object_name),
            )

        deleted = app_client.delete(f"/api/chat/thread/{thread['id']}", headers=headers)

        assert deleted.status_code == 200, deleted.text
        with psycopg.connect("postgresql://localhost:5432/datadeck_test", autocommit=True) as conn:
            assert conn.execute("SELECT COUNT(*) FROM run_events WHERE id=%s", (event_id,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM run_events WHERE id=%s", (orphan_event_id,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM agent_runs WHERE id=%s", (run_id,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM message_feedback WHERE run_id=%s", (run_id,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM thread_attachments WHERE thread_id=%s", (thread["id"],)).fetchone()[0] == 0
        assert not thread_dir.exists()

    def test_thread_isolation_between_users(self, app_client):
        """他人 thread 不可见（uid 过滤）；history 静默空不 403（§2.6 坑③）。"""
        headers_a = _login(app_client)
        th = app_client.post("/api/chat/thread",
                             json={"agent_id": "default-chatbot", "title": "A 的对话"},
                             headers=headers_a).json()

        # 制造第二个用户（initialize 已被 admin 占，直插测试库）
        import psycopg
        from server.utils.auth import hash_password
        with psycopg.connect("postgresql://localhost:5432/datadeck_test",
                             autocommit=True) as conn:
            conn.execute(
                "INSERT INTO users (username, uid, password_hash, role, created_at, is_deleted) "
                "VALUES (%s, %s, %s, %s, now(), 0)",
                ("userb", "userb", hash_password("b-pass-123"), "user"))
        login_b = app_client.post("/api/auth/token",
                                  data={"username": "userb", "password": "b-pass-123"})
        assert login_b.status_code == 200
        headers_b = {"Authorization": f"Bearer {login_b.json()['access_token']}"}

        listed_b = app_client.get("/api/chat/threads", headers=headers_b).json()
        assert all(t["id"] != th["id"] for t in listed_b)
        hist = app_client.get(f"/api/chat/thread/{th['id']}/history", headers=headers_b)
        assert hist.status_code == 200
        assert hist.json()["history"] == []

    def test_history_unknown_thread_silent(self, app_client):
        headers = _login(app_client)
        res = app_client.get("/api/chat/thread/nonexistent/history", headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["history"] == []


class TestRunsApi:
    def test_get_unknown_run_404(self, app_client):
        headers = _login(app_client)
        assert app_client.get("/api/agent/runs/nope", headers=headers).status_code == 404

    def test_active_run_none_when_no_runs(self, app_client):
        headers = _login(app_client)
        th = app_client.post("/api/chat/thread",
                             json={"agent_id": "default-chatbot", "title": "t"},
                             headers=headers).json()
        res = app_client.get(f"/api/chat/thread/{th['id']}/active-run", headers=headers)
        assert res.status_code == 200
        assert res.json()["run"] is None

    def test_create_run_rejects_unknown_thread_and_agent(self, app_client):
        headers = _login(app_client)
        unknown_thread = app_client.post(
            "/api/agent/runs",
            json={
                "query": "hi",
                "agent_slug": "default-chatbot",
                "thread_id": "missing",
            },
            headers=headers,
        )
        assert unknown_thread.status_code == 404

        thread = app_client.post(
            "/api/chat/thread",
            json={"agent_id": "default-chatbot", "title": "t"},
            headers=headers,
        ).json()
        unknown_agent = app_client.post(
            "/api/agent/runs",
            json={"query": "hi", "agent_slug": "missing", "thread_id": thread["id"]},
            headers=headers,
        )
        assert unknown_agent.status_code == 404

    def test_second_run_is_queued_and_request_id_routes_work(self, app_client):
        headers = _login(app_client)
        thread = app_client.post(
            "/api/chat/thread",
            json={"agent_id": "default-chatbot", "title": "queue"},
            headers=headers,
        ).json()

        import uuid
        import psycopg

        with psycopg.connect("postgresql://localhost:5432/datadeck_test", autocommit=True) as conn:
            conn.execute(
                "INSERT INTO agent_runs "
                "(id, thread_id, uid, agent_slug, status, source, channel, request_id, "
                "input_payload, token_usage, created_at, updated_at) "
                "VALUES (%s, %s, 'admin', 'default-chatbot', 'running', 'web', 'web', "
                "%s, '{}'::jsonb, '{}'::jsonb, now(), now())",
                (str(uuid.uuid4()), thread["id"], str(uuid.uuid4())),
            )

        request_id = str(uuid.uuid4())
        queued = app_client.post(
            "/api/agent/runs",
            json={
                "query": "later",
                "agent_slug": "default-chatbot",
                "thread_id": thread["id"],
                "meta": {"request_id": request_id},
                "queue_policy": "enqueue",
            },
            headers=headers,
        )
        assert queued.status_code == 200, queued.text
        assert queued.json()["status"] == "queued"
        assert queued.json()["request_id"] == request_id

        listed = app_client.get(
            f"/api/agent/thread/{thread['id']}/requests?agent_slug=default-chatbot",
            headers=headers,
        ).json()["requests"]
        assert any(item["request_id"] == request_id for item in listed)

        steered = app_client.post(
            f"/api/agent/requests/{request_id}/steer", headers=headers
        )
        assert steered.status_code == 200, steered.text
        cancelled = app_client.post(
            f"/api/agent/requests/{request_id}/cancel", headers=headers
        )
        assert cancelled.status_code == 200, cancelled.text


class TestAgentApi:
    def test_creation_draft_exposes_runtime_resources(self, app_client):
        headers = _login(app_client)
        res = app_client.get("/api/agent/configurable-items", headers=headers)
        assert res.status_code == 200, res.text
        items = res.json()["configurable_items"]
        assert {"model", "tools", "knowledges", "skills", "subagents"} <= set(items)
        assert "mcps" not in items
        assert any(
            option["slug"] == "package:sql" and option["kind"] == "package"
            for option in items["tools"]["options"]
        )

    def test_agent_detail_includes_builtin_tools(self, app_client):
        headers = _login(app_client)
        res = app_client.get("/api/agent/default-chatbot", headers=headers)
        assert res.status_code == 200
        tools = res.json()["agent"]["configurable_items"]["tools"]
        assert tools["options"]
        assert any(
            option["slug"] == "package:sql" and option["kind"] == "package"
            for option in tools["options"]
        )

    def test_agent_admin_can_manage_and_profile_update_persists(self, app_client):
        headers = _login(app_client)
        created = app_client.post(
            "/api/agent",
            json={
                "slug": "manageable-agent",
                "name": "Manageable Agent",
                "backend_id": "ChatbotAgent",
                "icon": "/uploads/agent/test.png",
                "share_config": {
                    "version": 2,
                    "read_scope": {"access_level": "user", "user_uids": []},
                    "manage_scope": None,
                },
                "execution_role": "standalone",
            },
            headers=headers,
        )
        assert created.status_code == 200, created.text
        agent_id = created.json()["agent"]["id"]

        listed = app_client.get("/api/agent", headers=headers).json()
        target = next(agent for agent in listed["agents"] if agent["id"] == agent_id)
        assert target["can_manage"] is True

        updated = app_client.put(
            f"/api/agent/{agent_id}",
            json={
                "name": "Updated Agent",
                "icon": "/uploads/agent/updated.png",
                "share_config": target["share_config"],
                "execution_role": "standalone",
            },
            headers=headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["agent"]["name"] == "Updated Agent"
        assert updated.json()["agent"]["icon"] == "/uploads/agent/updated.png"
        # 兼容早期前端已将 slug 当作操作标识的页面状态。
        assert app_client.get("/api/agent/manageable-agent", headers=headers).status_code == 200

        scheduled = app_client.post(
            "/api/scheduled-tasks",
            json={
                "name": "Agent schedule",
                "cron": "0 9 * * *",
                "prompt": "生成摘要",
                "agent_slug": "manageable-agent",
            },
            headers=headers,
        )
        assert scheduled.status_code == 200, scheduled.text
        assert app_client.delete(f"/api/agent/{agent_id}", headers=headers).status_code == 409
        assert app_client.delete(
            f"/api/scheduled-tasks/{scheduled.json()['id']}", headers=headers
        ).status_code == 200
        assert app_client.delete(f"/api/agent/{agent_id}", headers=headers).status_code == 200

    def test_agent_collaboration_roles_require_an_explicit_allowlist(self, app_client):
        headers = _login(app_client)
        child = app_client.post(
            "/api/agent",
            json={
                "slug": "test-research-child",
                "name": "测试研究子智能体",
                "execution_role": "subagent",
                "backend_id": "ChatbotAgent",
            },
            headers=headers,
        )
        assert child.status_code == 200, child.text
        assert child.json()["agent"]["execution_role"] == "subagent"

        invalid_parent = app_client.post(
            "/api/agent",
            json={
                "slug": "test-invalid-coordinator",
                "name": "无子项协调 Agent",
                "delegation_enabled": True,
            },
            headers=headers,
        )
        assert invalid_parent.status_code == 422, invalid_parent.text

        parent = app_client.post(
            "/api/agent",
            json={
                "slug": "test-coordinator",
                "name": "测试协调 Agent",
                "delegation_enabled": True,
                "config_json": {"context": {"subagents": ["test-research-child"]}},
            },
            headers=headers,
        )
        assert parent.status_code == 200, parent.text
        body = parent.json()["agent"]
        assert body["execution_role"] == "standalone"
        assert body["delegation_enabled"] is True
        assert app_client.delete(
            f"/api/agent/{child.json()['agent']['id']}", headers=headers
        ).status_code == 409

        unknown_child = app_client.post(
            "/api/agent",
            json={
                "slug": "test-unknown-coordinator",
                "name": "无效协作 Agent",
                "delegation_enabled": True,
                "config_json": {"context": {"subagents": ["not-a-child"]}},
            },
            headers=headers,
        )
        assert unknown_child.status_code == 422, unknown_child.text

    def test_agent_workflow_rejects_cyclic_dependencies(self, app_client):
        headers = _login(app_client)
        child = app_client.post(
            "/api/agent",
            json={
                "slug": "workflow-child",
                "name": "工作流子智能体",
                "execution_role": "subagent",
            },
            headers=headers,
        )
        assert child.status_code == 200, child.text
        workflow = {
            "nodes": [
                {"id": "a", "subagent_slug": "workflow-child", "task_template": "分析请求"},
            ],
            "edges": [{"source": "a", "target": "a"}],
        }
        response = app_client.post(
            "/api/agent",
            json={
                "slug": "cyclic-workflow-parent",
                "name": "循环工作流",
                "delegation_enabled": True,
                "config_json": {"context": {"subagents": ["workflow-child"], "subagent_workflow": workflow}},
            },
            headers=headers,
        )
        assert response.status_code == 422, response.text
