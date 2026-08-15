"""任务进展通知的数据结构定义（纯标准库，可独立单测）。

对应规划文档 plan-1.0.2-nanobot-notifications.md §7。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class TaskStatus(str, Enum):
    """任务状态。值即协议字符串，便于 MCP 输入直接映射。"""

    PENDING = "pending"      # 待执行
    RUNNING = "running"      # 执行中
    SUCCESS = "success"      # 成功
    FAILED = "failed"        # 失败
    SKIPPED = "skipped"      # 跳过
    CANCELED = "canceled"    # 取消


class TaskEventType(str, Enum):
    """任务事件类型。"""

    STARTED = "started"      # 开始
    PROGRESS = "progress"    # 进展
    COMPLETED = "completed"  # 完成
    ERROR = "error"          # 错误


# 状态 → 桌宠弹窗等级（对应 aipet.py 中 BUBBLE_STYLES 的 key）
STATUS_LEVEL = {
    TaskStatus.SUCCESS: "ok",
    TaskStatus.FAILED: "error",
    TaskStatus.CANCELED: "error",
    TaskStatus.SKIPPED: "warn",
    TaskStatus.RUNNING: "info",
    TaskStatus.PENDING: "info",
}

# 状态 → 气泡前导徽标
STATUS_BADGE = {
    TaskStatus.SUCCESS: "✅",
    TaskStatus.FAILED: "❌",
    TaskStatus.CANCELED: "⏹",
    TaskStatus.SKIPPED: "⏭",
    TaskStatus.RUNNING: "🔄",
    TaskStatus.PENDING: "⏳",
}

# 关键异常状态：豁免去重与频次限制，必弹（对应规划 §10）
CRITICAL_STATUSES = (TaskStatus.FAILED, TaskStatus.CANCELED)


@dataclass
class TaskNotification:
    """一条任务进展通知。

    source / task_id / task_name / status / event 为必填；
    status 与 event 允许以字符串传入，构造时自动转换为枚举，提升 MCP 输入容错。
    """

    source: str                       # 来源标识："nanobot" / "workbuddy" / 自定义
    task_id: str                     # 任务在来源内的唯一 ID
    task_name: str                   # 展示名，如「股票买卖点检测」
    status: TaskStatus
    event: TaskEventType = TaskEventType.COMPLETED
    summary: str = ""                # 一句话摘要
    progress: Optional[float] = None  # 进度 0~1，可选
    detail: str = ""                 # 详情（可点击展开）
    url: str = ""                    # 跳转链接（工作空间 / WebUI）
    timestamp: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        if isinstance(self.status, str):
            self.status = TaskStatus(self.status)
        if isinstance(self.event, str):
            self.event = TaskEventType(self.event)
        if self.progress is not None:
            try:
                self.progress = max(0.0, min(1.0, float(self.progress)))
            except (TypeError, ValueError):
                self.progress = None
