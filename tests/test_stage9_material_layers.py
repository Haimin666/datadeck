from pathlib import Path

from scripts.prepare_lion_dw_material import ast_metadata
from server.services.code_repository_service import _snapshot_file_metadata
from server.services.knowledge_service import BUSINESS_SOURCE_TYPES


def test_sql_ast_material_keeps_tables_and_columns():
    result = ast_metadata("SELECT a.user_id, b.amount FROM ods.user a JOIN dwd.pay b ON a.id=b.id")
    assert result["parse_status"] == "ok"
    assert any("ods.user" in item for item in result["ast_tables"])
    assert any("user_id" in item for item in result["ast_columns"])


def test_repository_snapshot_index_is_hashable_and_skips_binary(tmp_path: Path):
    (tmp_path / "job.sql").write_text("select id from dwd.user", encoding="utf-8")
    (tmp_path / "README.md").write_text("business notes", encoding="utf-8")
    (tmp_path / "secret.bin").write_bytes(b"\x00\x01")
    items = _snapshot_file_metadata(tmp_path, "abc123")
    assert {item["path"] for item in items} == {"job.sql", "README.md"}
    sql = next(item for item in items if item["path"] == "job.sql")
    assert sql["commit_sha"] == "abc123"
    assert sql["parse_status"] == "ok"
    assert any("dwd.user" in item for item in sql["tables_json"])


def test_default_rag_source_types_are_business_only():
    assert BUSINESS_SOURCE_TYPES == {"upload", "wiki", "business_doc"}
