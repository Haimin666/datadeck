"""BaseContext: Agent 可配置参数（自 Yuxi agents/context.py 抽离，去掉平台依赖）。

保留：可配置字段声明 + update/update_from_dict/get_configurable_items 通用工具 + 摘要/预算常量。
去掉：workspace/system_options/UserRepository/LITE 等平台 import（宿主经适配者注入）。
"""

from __future__ import annotations

import uuid
from dataclasses import MISSING, dataclass, field, fields
from typing import Any, get_origin

# ── 摘要/预算默认常量（自 Yuxi 原样迁移）─────────────────────────
DEFAULT_SUMMARY_THRESHOLD_K = 100            # 100K tokens 触发摘要
DEFAULT_SUMMARY_KEEP_MESSAGES = 10
DEFAULT_SUMMARY_TOOL_RESULT_TOKEN_LIMIT = 300
DEFAULT_SUMMARY_L2_TRIGGER_RATIO = 0.4
DEFAULT_MAX_EXECUTION_STEPS = 300
DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS = 3

DEFAULT_DATADECK_SUMMARY_PROMPT = """你是对话上下文压缩助手。
你的任务是把下面的对话历史压缩成后续智能体继续工作所需的高价值上下文。

请特别保留并清晰记录：

## SESSION INTENT
用户当前的主要目标、任务范围和最终交付物。

## USER REQUIREMENTS AND PREFERENCES
用户明确提出的要求、偏好、禁忌、输出格式、语言风格、技术约束、验收标准，以及对实现方式的取舍意见。只记录仍然可能影响后续回答或执行的内容。

## PROGRESS AND DECISIONS
已经完成的步骤、关键结论、已确认的方案、被否定的方案及原因。

## ARTIFACTS AND REFERENCES
已经创建、修改、读取或需要继续关注的文件、路径、工具输出路径、线程或运行标识。保留具体路径和关键标识符。

## NEXT STEPS
为了完成用户目标，后续最应该继续做的具体步骤。没有待办时写 None。

要求：
- 不要逐字复述冗长工具输出；保留结论、路径和必要证据。
- 不要编造没有出现在对话中的事实。
- 如果存在未解决的问题或风险，明确记录。
- 使用与用户主要对话一致的语言。

<messages>
{messages}
</messages>

只输出压缩后的上下文，不要添加额外说明。"""


@dataclass(kw_only=True)
class BaseContext:
    """可配置的 Agent 上下文基类（供各类 graph 继承）。

    配置优先级(顶层到低)：运行时输入(update_from_dict) > 类默认值。
    """

    def update(self, data: dict):
        """仅更新已声明的字段（忽略未知键）。"""
        declared_fields = {item.name for item in fields(self)}
        for key, value in data.items():
            if key in declared_fields:
                setattr(self, key, value)

    thread_id: str = field(
        default_factory=lambda: str(uuid.uuid4()),
        metadata={"name": "线程ID", "configurable": False, "description": "用来唯一标识一个对话线程"},
    )

    uid: str = field(
        default_factory=lambda: str(uuid.uuid4()),
        metadata={"name": "UID", "configurable": False, "description": "用来唯一标识一个用户"},
    )

    run_id: str | None = field(
        default=None,
        metadata={"name": "运行 ID", "configurable": False, "hide": True},
    )

    request_id: str | None = field(
        default=None,
        metadata={"name": "请求 ID", "configurable": False, "hide": True},
    )

    model: str = field(
        default="",
        metadata={"name": "模型", "description": "使用的聊天模型 spec (provider:model_id)", "type": "string"},
    )

    system_prompt: str = field(
        default="",
        metadata={"name": "系统提示词", "description": "追加到内置 prompt 之后的自定义系统提示", "type": "text"},
    )

    tools: list[str] | None = field(
        default=None,
        metadata={"name": "工具", "description": "启用的工具 slug 列表，None 表示全部可用", "type": "list", "kind": "tools"},
    )

    skills: list[str] | None = field(
        default=None,
        metadata={"name": "Skills", "description": "启用的 Skill slug 列表", "type": "list", "kind": "skills"},
    )

    preload_skills: list[str] | None = field(
        default=None,
        metadata={"name": "预加载 Skills", "description": "预加载的 Skill slug 列表", "type": "list", "kind": "skills"},
    )

    summary_threshold: int = field(
        default=DEFAULT_SUMMARY_THRESHOLD_K,
        metadata={"name": "上下文摘要触发阈值 (K)", "description": "超过该值(K tokens)启用摘要", "type": "number"},
    )

    summary_keep_messages: int = field(
        default=DEFAULT_SUMMARY_KEEP_MESSAGES,
        metadata={"name": "摘要后保留消息数", "description": "摘要触发后保留最近消息数", "type": "number"},
    )

    summary_prompt: str = field(
        default=DEFAULT_DATADECK_SUMMARY_PROMPT,
        metadata={"name": "上下文摘要提示词", "description": "摘要提示词，须含 {messages} 占位符", "type": "string"},
    )

    summary_tool_result_token_limit: int = field(
        default=DEFAULT_SUMMARY_TOOL_RESULT_TOKEN_LIMIT,
        metadata={"name": "摘要工具结果 token 上限", "type": "number"},
    )

    summary_l2_trigger_ratio: float = field(
        default=DEFAULT_SUMMARY_L2_TRIGGER_RATIO,
        metadata={"name": "L2 摘要触发比例", "type": "number"},
    )

    max_execution_steps: int = field(
        default=DEFAULT_MAX_EXECUTION_STEPS,
        metadata={"name": "最大执行步数", "description": "单次运行最大执行步数(recursion_limit)", "type": "number"},
    )

    model_retry_times: int = field(
        default=2,
        metadata={"name": "模型重试次数", "type": "number"},
    )

    @classmethod
    def _get_type_name(cls, field_type) -> str:
        origin = get_origin(field_type)
        if origin is not None:
            return getattr(origin, "__name__", str(origin))
        return getattr(field_type, "__name__", str(field_type))

    @classmethod
    def get_configurable_items(cls, user_role: str | None = None) -> dict:
        """可配置参数列表（供宿主 UI 用）。"""
        configurable_items: dict[str, dict] = {}
        for f in fields(cls):
            if f.init and not f.metadata.get("hide", False):
                if not f.metadata.get("configurable", True):
                    continue
                options = f.metadata.get("options", [])
                if callable(options):
                    options = options()
                configurable_items[f.name] = {
                    "type": f.metadata.get("type", cls._get_type_name(f.type)),
                    "name": f.metadata.get("name", f.name),
                    "options": options,
                    "default": f.default if f.default is not MISSING else (
                        f.default_factory() if f.default_factory is not MISSING else None
                    ),
                    "description": f.metadata.get("description", ""),
                    "kind": f.metadata.get("kind", ""),
                }
        return configurable_items

    def update_from_dict(self, data: dict):
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)