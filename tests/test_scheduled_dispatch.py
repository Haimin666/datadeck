"""定时任务调度去重与重启恢复测试。"""

from datetime import datetime

from server.services.task_service import _is_schedule_tick_claimed


def test_scheduled_dispatch_uses_persisted_minute_claim_after_restart():
    """同一分钟的持久化 last_run_at 必须阻止进程重启后的重复派发。"""
    tick = datetime(2026, 9, 14, 12, 30)
    persisted = tick.replace(second=42, microsecond=123)

    assert _is_schedule_tick_claimed(persisted, tick)


def test_scheduled_dispatch_allows_a_new_minute():
    tick = datetime(2026, 9, 14, 12, 31)
    previous = datetime(2026, 9, 14, 12, 30)

    assert not _is_schedule_tick_claimed(previous, tick)
