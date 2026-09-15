from types import SimpleNamespace

from datadeck.agents.buildin.chatbot.prompt import (
    build_capability_prompt,
    build_prompt_with_context,
)


def _tool(name: str):
    return SimpleNamespace(name=name)


def test_generic_agent_does_not_claim_unmounted_sync_capability():
    context = SimpleNamespace(
        identity_prompt="",
        system_prompt="",
        routing_hint="",
        sql_guard_enabled=False,
        _effective_skill_slugs=[],
    )

    prompt = build_prompt_with_context(context)
    capability = build_capability_prompt(context, [_tool("omd_get_table_schema")])

    assert "DataX" not in prompt
    assert "DataX" not in capability
    assert "查询库表结构和血缘" in capability
    assert "数据同步" not in capability


def test_sync_capability_requires_dba_skill_and_script_tool():
    context = SimpleNamespace(_effective_skill_slugs=["dba"])

    capability = build_capability_prompt(context, [_tool("run_skill_script")])

    assert "生成数据同步 SQL 和 DataX JSON" in capability

