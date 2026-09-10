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

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, JSON, String, Text

from server.models import Base
from server.utils.datetime_utils import utc_now_naive


class EvaluationCase(Base):
    """评测用例：问题 + 期望（工具序列/关键词）。"""

    __tablename__ = "evaluation_cases"
    __table_args__ = (Index("ix_eval_cases_dataset", "dataset"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset = Column(String(64), nullable=False, default="default")  # 评测集名
    question = Column(Text, nullable=False)
    expect_class = Column(String(32), nullable=False)  # metric/schema/data/chat
    expect_tools = Column(JSON, nullable=False, default=list)  # 期望被调用的工具名（任一）
    expect_keywords = Column(JSON, nullable=False, default=list)  # 回答必须含的关键词（命中即忠实）
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)


class EvaluationRun(Base):
    """一次批量评测的结果。"""

    __tablename__ = "evaluation_runs"

    id = Column(String(64), primary_key=True)
    dataset = Column(String(64), nullable=False, index=True)
    total = Column(Integer, nullable=False, default=0)
    passed = Column(Integer, nullable=False, default=0)
    tool_accuracy = Column(Integer, nullable=True)  # 百分比 0-100
    faithfulness = Column(Integer, nullable=True)  # 关键词命中百分比
    details = Column(JSON, nullable=False, default=list)  # 每 case 判定明细
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)


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
