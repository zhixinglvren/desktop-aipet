"""工作进展通知体系（1.0.2）。

桌宠作为常驻 MCP Server，对外暴露 notify_task_progress 工具；
Nanobot / WorkBuddy 等任意 Agent 作为 MCP Client 在任务生命周期内主动调用，
将执行状态与进展实时推送至桌面 AI 助理，以非阻塞弹窗呈现。

模块划分：
- types.py      ：数据结构与枚举（纯标准库）
- dedup.py      ：去重与频次控制策略（纯逻辑，可单测）
- bus.py        ：通知总线，连接 MCP 回调与桌宠弹窗
- mcp_server.py ：FastMCP streamable-http Server + Bearer 鉴权（依赖 mcp）
"""

from .types import (
    TaskStatus,
    TaskEventType,
    TaskNotification,
    STATUS_LEVEL,
    STATUS_BADGE,
    CRITICAL_STATUSES,
)
from .bus import NotificationBus
from .dedup import DedupPolicy, DedupTracker, Decision

__all__ = [
    "TaskStatus",
    "TaskEventType",
    "TaskNotification",
    "STATUS_LEVEL",
    "STATUS_BADGE",
    "CRITICAL_STATUSES",
    "NotificationBus",
    "DedupPolicy",
    "DedupTracker",
    "Decision",
]
