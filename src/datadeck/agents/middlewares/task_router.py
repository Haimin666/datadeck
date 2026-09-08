"""任务分类路由（阶段二 2.2）：确定性关键词预分类，注入 prompt 辅助工具选择。

四类：metric（口径）/ schema（结构）/ data（取数）/ chat（闲聊）。
纯规则、零 LLM 成本；分类不确定时返回 None，不干扰模型自主决策。
"""

from __future__ import annotations

import re

METRIC_PAT = re.compile(
    r"口径|怎么算|怎么定义|什么是|定义|什么意思|计算公式|指标|名词|概念|区别|含义"
)
SCHEMA_PAT = re.compile(
    r"有哪些表|表结构|字段|哪些库|血缘|上游|下游|依赖|schema|元数据|数据从哪|表清单|主题域"
)
DATA_PAT = re.compile(
    r"查询|查一下|帮我查|多少|几条|统计|汇总|列出|取数|明细|count|sum|avg|group by|最近|top"
)
CHAT_PAT = re.compile(r"^(你好|您好|hi|hello|嗨|在吗|谢谢|你是谁|介绍一下你自己)", re.IGNORECASE)


def classify_query(query: str) -> str | None:
    """返回 metric/schema/data/chat；无法确定返回 None。"""
    q = (query or "").strip()
    if not q:
        return None
    if CHAT_PAT.match(q):
        return "chat"
    if METRIC_PAT.search(q):
        return "metric"
    if SCHEMA_PAT.search(q):
        return "schema"
    if DATA_PAT.search(q):
        return "data"
    return None


_HINTS = {
    "metric": "本问题判定为【口径/概念类】→ 优先 rag_search 检索知识库作答，检索无果再通用回答。",
    "schema": "本问题判定为【表结构/元数据类】→ 优先 omd_* 工具查询真实元数据，禁止凭记忆编表名字段。",
    "data": "本问题判定为【取数类】→ 先 omd_get_table_schema 确认结构，再 sql_execute_query 执行并给出真实数值。",
    "chat": "本问题判定为【闲聊类】→ 直接回答，不要调用任何工具。",
}


def routing_hint(query: str) -> str:
    """生成可注入 system prompt 的路由提示（None 分类返回空串）。"""
    cls = classify_query(query)
    return _HINTS.get(cls, "")
