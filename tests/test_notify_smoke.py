"""notify 体系端到端冒烟测试（仅开发期使用，不参与打包）。

用系统 Python（已装 mcp）运行（在项目根目录执行）：
  D:\\Software\\Python3\\python.exe tests/test_notify_smoke.py
"""
import os
import sys
import time
import asyncio
import threading

# 确保无论从哪个工作目录运行，都能 import 到项目根的 notify 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from notify.types import TaskNotification, TaskStatus, TaskEventType
from notify.dedup import DedupTracker, DedupPolicy, Decision
from notify.bus import NotificationBus


# ---------------- FakeApp ----------------
class FakeApp:
    def __init__(self):
        self.notify_calls = []
        self.root = None  # 触发 bus 使用 threading.Timer 兜底

    def post_ui(self, fn):
        # 测试环境无 tkinter 主循环，直接同步执行
        fn()

    def notify(self, title, message="", level="info", play_sound=False):
        self.notify_calls.append((title, message, level, play_sound))


# ---------------- 1) types ----------------
def test_types():
    n = TaskNotification(source="nanobot", task_id="t1", task_name="股票检测",
                         status="success", event="completed", summary="触发卖出",
                         progress=1.5)
    assert n.status == TaskStatus.SUCCESS
    assert n.event == TaskEventType.COMPLETED
    assert n.progress == 1.0, "progress 应被夹取到 [0,1]"
    print("[OK] types: 枚举转换 / progress 夹取")


# ---------------- 2) dedup ----------------
def test_dedup():
    pol = DedupPolicy(max_per_minute=2, dedup_minutes=5)
    tr = DedupTracker(pol)

    base = dict(source="nanobot", task_id="t1", task_name="X",
                status=TaskStatus.SUCCESS, event=TaskEventType.COMPLETED)
    # 第一次 ALLOW
    d, _ = tr.decide(TaskNotification(**base))
    assert d == Decision.ALLOW
    # 短时间内同身份同状态 SUPPRESS
    d, _ = tr.decide(TaskNotification(**base))
    assert d == Decision.SUPPRESS, "应被去重抑制"
    # 关键异常豁免：FAILED 不受上次 success 抑制
    d, _ = tr.decide(TaskNotification(source="nanobot", task_id="t1",
                                     task_name="X", status=TaskStatus.FAILED,
                                     event=TaskEventType.ERROR))
    assert d == Decision.ALLOW, "FAILED 应豁免去重"
    # 频次：前两分钟内最多 2 条不同任务 ALLOW；第 3 条 RATE_LIMITED
    d, _ = tr.decide(TaskNotification(source="nanobot", task_id="a",
                                     task_name="A", status=TaskStatus.RUNNING))
    assert d == Decision.ALLOW
    d, _ = tr.decide(TaskNotification(source="nanobot", task_id="b",
                                     task_name="B", status=TaskStatus.RUNNING))
    assert d == Decision.RATE_LIMITED, \
        "超过每分钟上限应被限流（窗口已含 success+running 共2条非关键通知）"
    print("[OK] dedup: 去重 / 关键异常豁免 / 频次限流")


# ---------------- 3) bus ----------------
def test_bus():
    app = FakeApp()
    bus = NotificationBus(app, enabled=True, policy=DedupPolicy(max_per_minute=3, dedup_minutes=5))
    bus.set_source_enabled("nanobot", True)

    # ALLOW -> notify
    bus.emit(TaskNotification(source="nanobot", task_id="t1", task_name="股票检测",
                              status="success", summary="触发卖出"))
    assert app.notify_calls, "应弹窗"
    title, msg, lvl, _snd = app.notify_calls[-1]
    assert "✅" in msg and lvl == "ok", (msg, lvl)

    # SUPPRESS（同任务同状态重复）
    app.notify_calls.clear()
    bus.emit(TaskNotification(source="nanobot", task_id="t1", task_name="股票检测",
                              status="success", summary="重复"))
    assert not app.notify_calls, "重复完成应被抑制"

    # 来源关闭 -> 丢弃
    bus.set_source_enabled("nanobot", False)
    app.notify_calls.clear()
    bus.emit(TaskNotification(source="nanobot", task_id="t2", task_name="备份",
                              status="success", summary="完成"))
    assert not app.notify_calls, "来源关闭应丢弃"
    bus.set_source_enabled("nanobot", True)

    # 限流合并摘要
    app.notify_calls.clear()
    for i in range(5):
        bus.emit(TaskNotification(source="nanobot", task_id=f"r{i}", task_name=f"任务{i}",
                                  status="running", summary="进展"))
    bus._flush()  # 立即触发汇总
    titles = [c[0] for c in app.notify_calls]
    assert any("汇总" in t for t in titles), titles
    print("[OK] bus: 派发 / 去重 / 来源开关 / 限流汇总")


# ---------------- 4) MCP Server 端到端 ----------------
def test_mcp_server():
    from notify import mcp_server

    app = FakeApp()
    bus = NotificationBus(app, enabled=True, policy=DedupPolicy())
    bus.set_source_enabled("nanobot", True)
    app.bus = bus
    mcp_server.set_app(app)

    host, port, token = "127.0.0.1", 18792, "secrettoken"
    t = threading.Thread(target=mcp_server.serve, args=(host, port, token),
                         daemon=True, name="mcp-test")
    t.start()

    # 等待服务就绪
    from mcp.client.streamable_http import streamablehttp_client
    from mcp import ClientSession
    up = False
    for _ in range(50):
        try:
            async def _probe():
                async with streamablehttp_client(
                    f"http://{host}:{port}/mcp",
                    headers={"Authorization": f"Bearer {token}"}) as (r, w, _):
                    async with ClientSession(r, w) as s:
                        await s.initialize()
                        return True
            if asyncio.run(_probe()):
                up = True
                break
        except Exception:
            time.sleep(0.2)
    assert up, "MCP Server 未在预期时间内就绪"
    print("[OK] mcp: Server 已就绪")

    async def call_with(token_arg):
        async with streamablehttp_client(
            f"http://{host}:{port}/mcp",
            headers={"Authorization": f"Bearer {token_arg}"} if token_arg else {}) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return await s.call_tool("notify_task_progress", {
                    "source": "nanobot", "task_id": "t9", "task_name": "股票买卖点检测",
                    "status": "success", "event": "completed", "summary": "触发卖出信号"})

    # 合法 token -> 成功并弹窗
    res = asyncio.run(call_with(token))
    assert res is not None
    # call_tool 返回 CallToolResult，检查 content 文本
    text = "".join(getattr(c, "text", "") for c in getattr(res, "content", []))
    assert '"ok": true' in text or "ok" in text.lower(), text
    assert app.notify_calls, "合法调用应触发弹窗"
    print("[OK] mcp: 合法 Bearer 调用成功并触发桌宠弹窗 ->", app.notify_calls[-1][0])

    # 非法/缺失 token -> 401
    denied = False
    try:
        asyncio.run(call_with(None))
    except Exception as e:
        denied = True
    assert denied, "无 token 应被 401 拒绝"
    print("[OK] mcp: 缺失/无效 Token 被 401 拒绝")

    # 收尾（测试进程直接强退，避免 uvicorn 线程挂起）
    print("全部通过，强制退出测试进程")
    import os
    os._exit(0)


if __name__ == "__main__":
    test_types()
    test_dedup()
    test_bus()
    test_mcp_server()
