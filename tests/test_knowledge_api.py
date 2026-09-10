from __future__ import annotations


def _admin_headers(client):
    response = client.post("/api/auth/token", data={"username": "admin", "password": "admin123456"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


class TestKnowledgeApi:
    def test_text_build_query_and_delete_flow(self, app_client):
        headers = _admin_headers(app_client)
        created = app_client.post(
            "/api/knowledge/databases",
            json={"name": "文本测试库", "description": "基础资料"},
            headers=headers,
        )
        assert created.status_code == 200, created.text
        kb = created.json()

        uploaded = app_client.post(
            f"/api/knowledge/databases/{kb['id']}/documents/upload",
            files={"file": ("guide.md", "逾期率的计算口径是本金逾期金额除以应还本金。\n\nM1 表示逾期 1 到 30 天。".encode(), "text/markdown")},
            headers=headers,
        )
        assert uploaded.status_code == 200, uploaded.text
        document = uploaded.json()
        assert document["status"] in {"indexed", "indexed_keyword"}
        assert document["chunk_count"] >= 1

        queried = app_client.post(
            f"/api/knowledge/databases/{kb['id']}/query",
            json={"query": "逾期率", "top_k": 3},
            headers=headers,
        )
        assert queried.status_code == 200, queried.text
        assert queried.json()["results"][0]["document_id"] == document["id"]

        deleted = app_client.delete(
            f"/api/knowledge/databases/{kb['id']}/documents/{document['id']}", headers=headers
        )
        assert deleted.status_code == 200
        assert app_client.delete(f"/api/knowledge/databases/{kb['id']}", headers=headers).status_code == 200

    def test_non_text_upload_is_rejected(self, app_client):
        headers = _admin_headers(app_client)
        kb = app_client.post(
            "/api/knowledge/databases", json={"name": "格式测试库"}, headers=headers
        ).json()
        response = app_client.post(
            f"/api/knowledge/databases/{kb['id']}/documents/upload",
            files={"file": ("scan.pdf", b"not a pdf", "application/pdf")}, headers=headers,
        )
        assert response.status_code == 422
        app_client.delete(f"/api/knowledge/databases/{kb['id']}", headers=headers)

    def test_scheduled_task_crud(self, app_client):
        headers = _admin_headers(app_client)
        # 首次读取会初始化默认内置智能体；定时任务只能绑定已有智能体。
        assert app_client.get("/api/agent", headers=headers).status_code == 200
        created = app_client.post("/api/scheduled-tasks", json={
            "name": "每日摘要", "cron": "0 9 * * *", "prompt": "生成今日摘要",
        }, headers=headers)
        assert created.status_code == 200, created.text
        task_id = created.json()["id"]
        assert any(item["id"] == task_id for item in app_client.get("/api/scheduled-tasks", headers=headers).json()["tasks"])
        updated = app_client.put(f"/api/scheduled-tasks/{task_id}", json={
            "name": "工作日摘要", "cron": "0 10 * * 1-5", "prompt": "生成工作日摘要",
            "enabled": False,
        }, headers=headers)
        assert updated.status_code == 200
        assert updated.json()["enabled"] is False
        assert app_client.delete(f"/api/scheduled-tasks/{task_id}", headers=headers).status_code == 200
