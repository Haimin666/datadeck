"""评测体系（阶段四 4.2）：评测集 + 批量跑分，量化迭代依据。

设计：
- 评测集存 PG（evaluation_cases 表）：question + expect_class(口径/结构/取数/闲聊)
  + expect_keywords(回答必须包含的关键词) + expect_tools(期望工具序列)
- 跑分：对每个 case 走真实链路（create run → SSE 收集），自动判定：
  * 工具选择准确率：实际调用工具 ∩ 期望工具
  * 回答忠实度：期望关键词命中率（口径关键词必须出现=有依据）
- 结果写 evaluation_runs 表，支持多次跑分对比。
"""

from __future__ import annotations

def judge_case(answer_text: str, called_tools: list[str],
               expect_tools: list[str], expect_keywords: list[str]) -> dict:
    """单 case 判定：工具命中 + 关键词命中。"""
    tool_hit = (not expect_tools) or any(
        any(t in called for called in called_tools) for t in expect_tools
    )
    kw_hits = [k for k in expect_keywords if k in (answer_text or "")]
    kw_hit = (not expect_keywords) or len(kw_hits) >= max(1, len(expect_keywords) // 2)
    return {
        "tool_hit": tool_hit,
        "keyword_hits": kw_hits,
        "keyword_hit": kw_hit,
        "passed": tool_hit and kw_hit,
    }
