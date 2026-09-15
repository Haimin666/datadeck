from datadeck.agents.toolkits import omd_client


def test_lineage_parses_current_openmetadata_edges(monkeypatch):
    target = "数仓HIVE.default.lion_dw_app.app_atlas_gl_map_overdue_vin_df"
    monkeypatch.setattr(omd_client, "omd_configured", lambda: True)
    monkeypatch.setattr(
        omd_client,
        "omd_service",
        lambda: "数仓HIVE",
    )
    monkeypatch.setattr(omd_client, "omd_database", lambda: "default")
    monkeypatch.setattr(
        omd_client,
        "_api_get",
        lambda path: {
            "edges": [
                {
                    "fromEntity": {"fqn": "数仓HIVE.default.lion_dw_dwd.upstream"},
                    "toEntity": {"fqn": target},
                },
                {
                    "fromEntity": {"fqn": target},
                    "toEntity": {"fqn": "数仓HIVE.default.lion_dw_app.downstream"},
                },
            ]
        },
    )

    result = omd_client.get_table_lineage(
        "lion_dw_app",
        "app_atlas_gl_map_overdue_vin_df",
        direction="both",
        database="default",
        service="数仓HIVE",
    )

    assert result["ok"] is True
    assert result["upstream"] == ["数仓HIVE.default.lion_dw_dwd.upstream"]
    assert result["downstream"] == ["数仓HIVE.default.lion_dw_app.downstream"]


def test_lineage_keeps_legacy_directional_response(monkeypatch):
    monkeypatch.setattr(omd_client, "omd_configured", lambda: True)
    monkeypatch.setattr(omd_client, "omd_service", lambda: "svc")
    monkeypatch.setattr(omd_client, "omd_database", lambda: "db")
    monkeypatch.setattr(
        omd_client,
        "_api_get",
        lambda path: {
            "upstreamEdges": [{"fromEntity": {"fullyQualifiedName": "svc.db.s.up"}}],
            "downstreamEdges": [{"toEntity": {"fullyQualifiedName": "svc.db.s.down"}}],
        },
    )

    result = omd_client.get_table_lineage("s", "t", direction="both")

    assert result["upstream"] == ["svc.db.s.up"]
    assert result["downstream"] == ["svc.db.s.down"]


def test_lineage_walks_current_edges_to_requested_depth(monkeypatch):
    target = "svc.db.s.target"
    monkeypatch.setattr(omd_client, "omd_configured", lambda: True)
    monkeypatch.setattr(omd_client, "omd_service", lambda: "svc")
    monkeypatch.setattr(omd_client, "omd_database", lambda: "db")
    monkeypatch.setattr(
        omd_client,
        "_api_get",
        lambda path: {
            "edges": [
                {"fromEntity": {"fqn": "svc.db.s.up2"}, "toEntity": {"fqn": "svc.db.s.up1"}},
                {"fromEntity": {"fqn": "svc.db.s.up1"}, "toEntity": {"fqn": target}},
                {"fromEntity": {"fqn": target}, "toEntity": {"fqn": "svc.db.s.down1"}},
                {"fromEntity": {"fqn": "svc.db.s.down1"}, "toEntity": {"fqn": "svc.db.s.down2"}},
            ]
        },
    )

    result = omd_client.get_table_lineage("s", "target", depth=2, direction="both")

    assert result["upstream"] == ["svc.db.s.up1", "svc.db.s.up2"]
    assert result["downstream"] == ["svc.db.s.down1", "svc.db.s.down2"]
