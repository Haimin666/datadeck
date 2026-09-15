from datadeck.agents.toolkits import omd_client


def test_service_type_normalization_accepts_only_hive_and_doris():
    assert omd_client.normalize_service_type("Hive") == "Hive"
    assert omd_client.normalize_service_type("数仓Doris") == "Doris"
    assert omd_client.normalize_service_type("") is None
    assert omd_client.normalize_service_type("MySQL") == "Mysql"
    assert omd_client.normalize_service_type("Postgres") is None


def test_list_services_filters_by_service_type(monkeypatch):
    monkeypatch.setattr(omd_client, "omd_configured", lambda: True)
    monkeypatch.setattr(omd_client, "_api_get", lambda path: {
        "data": [
            {"name": "数仓HIVE", "fullyQualifiedName": "数仓HIVE", "serviceType": "Hive"},
            {"name": "数仓Doris", "fullyQualifiedName": "数仓Doris", "serviceType": "Doris"},
        ]
        if "databaseServices" in path else []
    })

    result = omd_client.list_services("hive")

    assert result["service_type"] == "Hive"
    assert [item["name"] for item in result["services"]] == ["数仓HIVE"]
    assert result["supported_service_types"] == [
        "Hive", "Doris", "Mysql", "Oracle", "Looker", "CustomDashboard",
    ]


def test_list_services_includes_dashboard_category_and_types(monkeypatch):
    monkeypatch.setattr(omd_client, "omd_configured", lambda: True)
    monkeypatch.setattr(omd_client, "_api_get", lambda path: {
        "data": [
            {"name": "Stingray分享看板", "serviceType": "Looker"},
            {"name": "帆软Report报表", "serviceType": "CustomDashboard"},
        ]
        if "dashboardServices" in path else []
    })

    result = omd_client.list_services(service_category="dashboard")

    assert result["service_category"] == "dashboard"
    assert {(item["name"], item["service_type"]) for item in result["services"]} == {
        ("Stingray分享看板", "Looker"),
        ("帆软Report报表", "CustomDashboard"),
    }


def test_search_tables_returns_service_type_and_filters_candidates(monkeypatch):
    monkeypatch.setattr(omd_client, "omd_configured", lambda: True)
    monkeypatch.setattr(omd_client, "_api_get", lambda path: (
        {"data": [
            {"name": "数仓HIVE", "serviceType": "Hive"},
            {"name": "数仓Doris", "serviceType": "Doris"},
        ]}
        if path.startswith("/services/databaseServices") else
        {"hits": [
            {"_source": {"name": "t_hive", "fullyQualifiedName": "数仓HIVE.default.app.t_hive"}},
            {"_source": {"name": "t_doris", "fullyQualifiedName": "数仓Doris.default.app.t_doris"}},
        ]}
    ))

    result = omd_client.search_tables("t", service_type="Doris")

    assert result["total"] == 1
    assert result["candidates"][0]["name"] == "t_doris"
    assert result["candidates"][0]["service_type"] == "Doris"


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
