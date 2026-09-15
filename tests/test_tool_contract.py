from types import SimpleNamespace

from datadeck.agents.toolkits.packages import (
    expand_tool_selection,
    mcp_package_options,
    mcp_server_slug_from_package,
    package_options,
)
from datadeck.agents.toolkits.registry import (
    ToolExtraMetadata,
    get_all_extra_metadata,
    get_all_tool_instances,
    register_tool,
    unregister_tool,
)
from datadeck.agents.toolkits.service import get_tool_metadata
from server.routers.agent_router import _configurable_items
from server.services.agent_runtime_tools import TOOL_REQUIRED_MODULES


def test_package_descriptor_uses_one_canonical_shape():
    options = package_options(fixed_packages={"package:omd"})

    assert options
    assert all(item["kind"] == "package" for item in options)
    assert all("slug" in item and "value" not in item for item in options)
    omd = next(item for item in options if item["slug"] == "package:omd")
    assert omd["fixed"] is True
    assert omd["configurable"] is False


def test_platform_package_is_fixed_for_both_builtin_agent_editors():
    for backend_id in ("ChatbotAgent", "DataAgent"):
        options = _configurable_items(backend_id=backend_id)["tools"]["options"]
        platform = next(item for item in options if item["slug"] == "package:platform")
        assert platform["fixed"] is True
        assert platform["configurable"] is False


def test_registered_tools_have_runtime_module_guards():
    registered = {item["slug"] for item in get_tool_metadata() if item.get("slug")}
    assert registered <= set(TOOL_REQUIRED_MODULES)


def test_package_selection_expands_and_deduplicates():
    expanded = expand_tool_selection(["package:omd", "omd_search_tables", "package:sql"])

    assert expanded.count("omd_search_tables") == 1
    assert expanded[-2:] == ["sql_validate", "sql_execute_query"]


def test_mcp_server_is_configured_as_dynamic_package():
    options = mcp_package_options([SimpleNamespace(
        slug="analytics", name="Analytics", description="metadata service",
    )])

    assert options[0]["slug"] == "package:mcp:analytics"
    assert mcp_server_slug_from_package(options[0]["slug"]) == "analytics"
    assert expand_tool_selection([options[0]["slug"]]) == []


def test_unknown_tool_selection_is_not_a_second_configuration_format():
    assert expand_tool_selection(["not-a-package"]) == []


def test_registry_returns_protected_snapshots_and_replaces_duplicate_slug():
    first = SimpleNamespace(name="test_contract_tool")
    second = SimpleNamespace(name="test_contract_tool")
    metadata = ToolExtraMetadata(category="buildin", tags=["test"])
    try:
        register_tool(first, metadata)
        register_tool(second, metadata)

        instances = get_all_tool_instances()
        assert isinstance(instances, tuple)
        assert [item for item in instances if item.name == "test_contract_tool"] == [second]

        copied = get_all_extra_metadata()["test_contract_tool"]
        copied.tags.append("mutated")
        assert "mutated" not in get_all_extra_metadata()["test_contract_tool"].tags
    finally:
        unregister_tool("test_contract_tool")
