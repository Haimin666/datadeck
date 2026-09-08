"""工具级熔断器（阶段三 3.1）：连续失败 → 熔断 open → 冷却后半开试探。

- 每个工具名独立计数；窗口内连续失败 ≥ threshold 触发熔断。
- 熔断中的调用直接返回降级提示（模型可读，可切换其他工具/如实告知用户）。
- cooldown 秒后半开：放一次请求试探，成功关闭熔断，失败重新计时。
- 供执行层（sql_executor/omd_client/rag_store）以装饰器/包装方式接入。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class _BreakerState:
    consecutive_failures: int = 0
    opened_at: float | None = None
    half_open: bool = False
    last_error: str = ""


@dataclass
class ToolCircuitBreaker:
    threshold: int = 3
    cooldown_seconds: float = 60.0

    _states: dict[str, _BreakerState] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _state(self, key: str) -> _BreakerState:
        if key not in self._states:
            self._states[key] = _BreakerState()
        return self._states[key]

    def check(self, key: str) -> tuple[bool, str]:
        """调用前检查：返回 (允许?, 降级提示)。"""
        with self._lock:
            st = self._state(key)
            if st.opened_at is None:
                return True, ""
            elapsed = time.monotonic() - st.opened_at
            if elapsed >= self.cooldown_seconds:
                st.half_open = True  # 放行一次试探
                return True, ""
            remain = int(self.cooldown_seconds - elapsed)
            return False, (
                f"工具 {key} 处于熔断状态（连续失败 {st.consecutive_failures} 次），"
                f"约 {remain}s 后自动恢复。请勿继续调用该工具，改用其他方式回答或如实告知用户服务暂不可用。"
                f"上次错误: {st.last_error[:150]}"
            )

    def record_success(self, key: str) -> None:
        with self._lock:
            st = self._state(key)
            st.consecutive_failures = 0
            st.opened_at = None
            st.half_open = False

    def record_failure(self, key: str, error: str = "") -> None:
        with self._lock:
            st = self._state(key)
            st.consecutive_failures += 1
            st.last_error = error
            if st.consecutive_failures >= self.threshold:
                st.opened_at = time.monotonic()

    def guard(self, key: str, fn: Callable[..., Any], *args, **kwargs) -> Any:
        """同步包装：检查 → 执行 → 记录。fn 抛异常记失败并原样抛出（由工具层收敛）。"""
        allowed, hint = self.check(key)
        if not allowed:
            return {"ok": False, "degraded": True, "error": hint}
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            self.record_failure(key, str(exc))
            raise
        if isinstance(result, dict) and result.get("ok") is False:
            self.record_failure(key, str(result.get("error", ""))[:200])
        else:
            self.record_success(key)
        return result

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {
                k: {
                    "consecutive_failures": s.consecutive_failures,
                    "open": s.opened_at is not None,
                    "half_open": s.half_open,
                    "last_error": s.last_error[:100],
                }
                for k, s in self._states.items()
            }


# 进程级默认实例
default_breaker = ToolCircuitBreaker()


async def aguard(key: str, coro_factory: Callable[[], Any]) -> Any:
    """异步包装：检查 → await 执行 → 记录。dict 结果按 ok 字段判定成败。"""
    allowed, hint = default_breaker.check(key)
    if not allowed:
        return {"ok": False, "degraded": True, "error": hint}
    try:
        result = await coro_factory()
    except Exception as exc:  # noqa: BLE001
        default_breaker.record_failure(key, str(exc))
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    if isinstance(result, dict) and result.get("ok") is False:
        if result.get("business"):
            # 业务性结果（如"表不存在"）≠ 服务故障，不触发熔断计数
            default_breaker.record_success(key)
        else:
            default_breaker.record_failure(key, str(result.get("error", ""))[:200])
    else:
        default_breaker.record_success(key)
    return result
