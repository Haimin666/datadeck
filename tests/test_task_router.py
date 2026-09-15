from datadeck.agents.middlewares.task_router import classify_query, routing_hint


def test_chat_classifier_requires_a_greeting_only():
    assert classify_query("你好") == "chat"
    assert classify_query("你好，帮我读取文件") is None


def test_code_logic_classifier_has_a_dedicated_route():
    assert classify_query("查看这个任务的数仓代码逻辑") == "code"
    assert "code_search" in routing_hint("查看这个任务的数仓代码逻辑")
    assert classify_query("查看这张表的字段注释") == "schema"
