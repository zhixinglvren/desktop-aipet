"""通知总线：连接 MCP 工具回调与桌宠弹窗。

职责（对应规划 §6、§9、§10）：
- 接收 TaskNotification（由 MCP 工具经 app.post_ui 在主线程回调本方法）。
- 经 dedup 去重 + 频次控制后，调用 app.notify(title, message, level) 统一弹窗。
- 频次超限时，将多条同类通知在 ~1.5s 内合并为一条「进展汇总」。

设计要点：
- emit() 假设运行在 tkinter 主线程（由 app.post_ui 保证），可直接调用 app.notify。
- 来源开关（按来源启用/禁用）与总开关由此处持有，并持久化到 config。
"""

import logging
import threading
from datetime import datetime, timedelta

from .types import TaskNotification, STATUS_LEVEL, STATUS_BADGE
from .dedup import DedupTracker, DedupPolicy, Decision

log = logging.getLogger("notify.bus")

# 汇总缓冲窗口：多少秒内到达的多条受限通知合并为一条
SUMMARY_WINDOW_S = 1.5


class NotificationBus:
    def __init__(self, app, enabled: bool = True, policy: DedupPolicy = None):
        self.app = app
        self.enabled = bool(enabled)
        self.policy = policy or DedupPolicy()
        self.tracker = DedupTracker(self.policy)
        self.source_enabled = {}       # source -> bool（缺省视为启用）
        self._pending = {}             # 汇总缓冲：key -> {"count", "sample"}
        self._flush_timer = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 开关
    # ------------------------------------------------------------------
    def set_source_enabled(self, source: str, on: bool):
        if source:
            self.source_enabled[str(source).lower()] = bool(on)

    def is_source_enabled(self, source: str) -> bool:
        return self.source_enabled.get(str(source).lower(), True)

    def set_enabled(self, on: bool):
        self.enabled = bool(on)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def emit(self, n: TaskNotification):
        if not self.enabled:
            return
        if not self.is_source_enabled(n.source):
            log.debug("来源已关闭，丢弃通知: %s", n.source)
            return

        decision, key = self.tracker.decide(n)
        if decision == Decision.SUPPRESS:
            log.debug("去重抑制: %s", key)
            return
        if decision == Decision.RATE_LIMITED:
            self._buffer_summary(n, key)
            return
        self._dispatch(n)

    # ------------------------------------------------------------------
    # 频次超限 → 合并汇总
    # ------------------------------------------------------------------
    def _buffer_summary(self, n: TaskNotification, key: str):
        with self._lock:
            buf = self._pending.setdefault(key, {"count": 0, "sample": n})
            buf["count"] += 1
            buf["sample"] = n
            if self._flush_timer is None:
                self._schedule_flush()
        log.debug("频次受限，合并为汇总: %s (共%d)", key, self._pending[key]["count"])

    def _schedule_flush(self):
        # emit 运行在 tkinter 主线程（经 post_ui），可用 root.after 定时回主线程
        root = getattr(self.app, "root", None)
        if root is not None and hasattr(root, "after"):
            self._flush_timer = root.after(int(SUMMARY_WINDOW_S * 1000), self._flush)
        else:
            # 非 GUI / 测试环境：用线程定时器兜底
            self._flush_timer = threading.Timer(SUMMARY_WINDOW_S, self._flush)
            self._flush_timer.daemon = True
            self._flush_timer.start()

    def _flush(self):
        with self._lock:
            self._flush_timer = None
            pending = self._pending
            self._pending = {}
        for _key, buf in pending.items():
            count = buf["count"]
            sample = buf["sample"]
            title = "📨 任务进展汇总"
            msg = (f"近 {int(SUMMARY_WINDOW_S)} 秒内有 {count} 条来自"
                   f"「{sample.source}」的新进展（示例：{sample.task_name}）")
            self.app.notify(title, msg, "info")

    # ------------------------------------------------------------------
    # 派发到桌宠弹窗
    # ------------------------------------------------------------------
    def _dispatch(self, n: TaskNotification):
        title = n.task_name or n.source
        badge = STATUS_BADGE.get(n.status, "ℹ️")
        msg = f"{badge} {n.summary}".rstrip()
        if not msg:
            msg = f"{badge} {n.event.value}"
        if n.progress is not None:
            msg += f" ({int(n.progress * 100)}%)"
        level = STATUS_LEVEL.get(n.status, "info")
        self.app.notify(title, msg, level)
