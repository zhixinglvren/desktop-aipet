"""通知去重与频次控制策略（纯逻辑，可独立单测）。

对应规划文档 §10：
- 按身份去重：key = source:task_id:日期，同日同任务重复完成则抑制。
- 同状态抑制：收到 success 后再收 success 不重复弹（除非间隔 > dedup_minutes）。
- 频次上限：默认每分钟最多 max_per_minute 条，超出由 bus 合并为汇总。
- 关键异常（FAILED / CANCELED）豁免去重与频次限制，必弹。

判定结果由 Decision 表示，供 bus 决定 ALLOW / SUPPRESS / RATE_LIMITED。
"""

import threading
from datetime import datetime, timedelta

from .types import TaskNotification, CRITICAL_STATUSES


class Decision:
    ALLOW = "allow"
    SUPPRESS = "suppress"          # 同身份同状态近期已发，抑制
    RATE_LIMITED = "rate_limited"  # 超过每分钟上限，由 bus 合并汇总


class DedupPolicy:
    def __init__(self, max_per_minute: int = 3, dedup_minutes: int = 5):
        self.max_per_minute = max(1, int(max_per_minute))
        self.dedup_minutes = max(0, int(dedup_minutes))


class DedupTracker:
    def __init__(self, policy: DedupPolicy = None):
        self.policy = policy or DedupPolicy()
        self._last_emit = {}     # key -> datetime（用于同身份去重）
        self._window = []        # [datetime]（用于每分钟频次统计）
        self._lock = threading.Lock()

    @staticmethod
    def _key(n: TaskNotification) -> str:
        day = n.timestamp.strftime("%Y-%m-%d")
        return f"{n.source}:{n.task_id}:{day}"

    def decide(self, n: TaskNotification):
        """返回 (Decision, key)。线程安全。"""
        with self._lock:
            now = datetime.now()
            critical = n.status in CRITICAL_STATUSES
            key = self._key(n)

            # 1) 同身份 + 同状态去重（关键异常豁免）
            last = self._last_emit.get(key)
            if (not critical) and last and (now - last) < timedelta(minutes=self.policy.dedup_minutes):
                return Decision.SUPPRESS, key

            # 2) 每分钟频次上限（关键异常豁免）
            if not critical:
                cutoff = now - timedelta(minutes=1)
                self._window = [t for t in self._window if t >= cutoff]
                if len(self._window) >= self.policy.max_per_minute:
                    return Decision.RATE_LIMITED, key
                self._window.append(now)

            # 3) 通过
            self._last_emit[key] = now
            return Decision.ALLOW, key

    def reset(self):
        """清空去重与频次计数（用户手动「重置去重」时调用）。"""
        with self._lock:
            self._last_emit.clear()
            self._window.clear()
