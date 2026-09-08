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


class TestThreadsApi:
    def test_thread_crud(self, app_client):
        headers = _login(app_client)
        created = app_client.post(
            "/api/chat/thread",
            json={"agent_id": "default-chatbot", "title": "新对话"},
            headers=headers).json()["thread"]
        assert created["title"] == "新对话"

        listed = app_client.get("/api/chat/threads", headers=headers).json()["threads"]
        assert any(t["id"] == created["id"] for t in listed)

        updated = app_client.put(
            f"/api/chat/thread/{created['id']}",
            json={"title": "改名", "is_pinned": True}, headers=headers).json()["thread"]
        assert updated["title"] == "改名"
        assert updated["is_pinned"] is True

        deleted = app_client.delete(f"/api/chat/thread/{created['id']}", headers=headers)
        assert deleted.status_code == 200

    def test_thread_isolation_between_users(self, app_client):
        """他人 thread 不可见（uid 过滤）；history 静默空不 403（§2.6 坑③）。"""
        headers_a = _login(app_client)
        th = app_client.post("/api/chat/thread",
                             json={"agent_id": "default-chatbot", "title": "A 的对话"},
                             headers=headers_a).json()["thread"]

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

        listed_b = app_client.get("/api/chat/threads", headers=headers_b).json()["threads"]
        assert all(t["id"] != th["id"] for t in listed_b)
        hist = app_client.get(f"/api/chat/thread/{th['id']}/history", headers=headers_b)
        assert hist.status_code == 200
        assert hist.json()["messages"] == []

    def test_history_unknown_thread_silent(self, app_client):
        headers = _login(app_client)
        res = app_client.get("/api/chat/thread/nonexistent/history", headers=headers)
        assert res.status_code == 200
        assert res.json() == {"messages": [], "last_seq": "0-0"}


class TestRunsApi:
    def test_get_unknown_run_404(self, app_client):
        headers = _login(app_client)
        assert app_client.get("/api/agent/runs/nope", headers=headers).status_code == 404

    def test_active_run_none_when_no_runs(self, app_client):
        headers = _login(app_client)
        th = app_client.post("/api/chat/thread",
                             json={"agent_id": "default-chatbot", "title": "t"},
                             headers=headers).json()["thread"]
        res = app_client.get(f"/api/chat/thread/{th['id']}/active-run", headers=headers)
        assert res.status_code == 200
        assert res.json()["run"] is None
