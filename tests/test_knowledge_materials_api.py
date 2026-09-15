"""KnowledgeDocument is the only HTTP-facing RAG material contract."""

from tests.test_auth_runs_api import _login


def test_knowledge_material_preview_and_delete_use_unified_document_api(app_client):
    headers = _login(app_client)
    created = app_client.post(
        "/api/knowledge/databases",
        json={"name": "统一物料测试", "description": "test", "access_scope": "shared"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    kb_id = created.json()["id"]

    uploaded = app_client.post(
        f"/api/knowledge/databases/{kb_id}/documents/upload",
        files={"file": ("definition.md", "逾期率 = 逾期金额 / 应还金额".encode(), "text/markdown")},
        headers=headers,
    )
    assert uploaded.status_code == 200, uploaded.text
    document_id = uploaded.json()["id"]

    listed = app_client.get("/api/knowledge/materials?limit=10000", headers=headers)
    assert listed.status_code == 200, listed.text
    material = next(item for item in listed.json()["items"] if item["id"] == document_id)
    assert material["source_type"] == "upload"
    assert "逾期率" in material["content_preview"]

    preview = app_client.get(f"/api/knowledge/materials/{document_id}", headers=headers)
    assert preview.status_code == 200, preview.text
    assert "逾期率" in preview.json()["content"]

    deleted = app_client.delete(
        f"/api/knowledge/databases/{kb_id}/documents/{document_id}", headers=headers,
    )
    assert deleted.status_code == 200, deleted.text
    assert app_client.get(f"/api/knowledge/materials/{document_id}", headers=headers).status_code == 404
