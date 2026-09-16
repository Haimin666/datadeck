"""按模型上下文窗口计算 Agent 的运行预算。"""

from __future__ import annotations

from dataclasses import dataclass
import os


DEFAULT_CONTEXT_WINDOW = 128 * 1024
DEFAULT_OPERATIONAL_CAP = 256 * 1024
MIN_OUTPUT_RESERVE = 16 * 1024
MAX_OUTPUT_RESERVE = 128 * 1024


@dataclass(frozen=True, slots=True)
class ContextBudget:
    model_context_window: int
    effective_context_window: int
    output_reserve: int
    soft_budget: int
    summary_trigger: int
    hard_limit: int

    def to_dict(self) -> dict[str, int]:
        return {
            "model_context_window": self.model_context_window,
            "effective_context_window": self.effective_context_window,
            "output_reserve": self.output_reserve,
            "soft_budget": self.soft_budget,
            "summary_trigger": self.summary_trigger,
            "hard_limit": self.hard_limit,
        }


def _positive_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def resolve_context_budget(
    context_window: int | None,
    max_output_tokens: int | None = None,
) -> ContextBudget:
    """保留输出空间，并用部署上限控制实际运行成本。"""
    model_window = int(context_window or DEFAULT_CONTEXT_WINDOW)
    if model_window <= 0:
        model_window = DEFAULT_CONTEXT_WINDOW
    operational_cap = _positive_env("DATADECK_CONTEXT_OPERATIONAL_CAP", DEFAULT_OPERATIONAL_CAP)
    effective = min(model_window, operational_cap)
    output_reserve = max(MIN_OUTPUT_RESERVE, int(effective * 0.20))
    if max_output_tokens and max_output_tokens > 0:
        output_reserve = min(output_reserve, int(max_output_tokens))
    output_reserve = min(output_reserve, MAX_OUTPUT_RESERVE)
    available = max(effective - output_reserve, MIN_OUTPUT_RESERVE)
    soft = max(MIN_OUTPUT_RESERVE, int(available * 0.72))
    summary = max(soft + 1, int(available * 0.82))
    hard = max(summary + 1, int(available * 0.90))
    return ContextBudget(
        model_context_window=model_window,
        effective_context_window=effective,
        output_reserve=output_reserve,
        soft_budget=min(soft, available),
        summary_trigger=min(summary, available),
        hard_limit=min(hard, available),
    )


__all__ = ["ContextBudget", "resolve_context_budget"]
