from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from datadeck.agents.middlewares.tool_failure_guard import (
    MAX_CONSECUTIVE_TOOL_FAILURES,
    ToolFailureGuardMiddleware,
)


def _request():
    context = SimpleNamespace()
    return SimpleNamespace(
        tool_call={"name": "workspace_write_file", "id": "call-1", "args": {}},
        runtime=SimpleNamespace(context=context),
    )


@pytest.mark.asyncio
async def test_tool_failure_is_returned_to_model_before_fifth_failure():
    middleware = ToolFailureGuardMiddleware()
    request = _request()

    async def fail(_request):
        return ToolMessage(content="缺少 path 和 content", tool_call_id="call-1", status="error")

    for attempt in range(1, MAX_CONSECUTIVE_TOOL_FAILURES):
        result = await middleware.awrap_tool_call(request, fail)
        assert isinstance(result, ToolMessage)
        assert f"第 {attempt} 次" in result.content

    with pytest.raises(RuntimeError, match="连续失败 5 次"):
        await middleware.awrap_tool_call(request, fail)


@pytest.mark.asyncio
async def test_success_clears_tool_failure_count():
    middleware = ToolFailureGuardMiddleware()
    request = _request()

    async def fail(_request):
        return ToolMessage(content="参数错误", tool_call_id="call-1", status="error")

    async def succeed(_request):
        return "ok"

    await middleware.awrap_tool_call(request, fail)
    assert request.runtime.context._tool_failure_counts["workspace_write_file"] == 1
    assert await middleware.awrap_tool_call(request, succeed) == "ok"
    assert request.runtime.context._tool_failure_counts == {}
