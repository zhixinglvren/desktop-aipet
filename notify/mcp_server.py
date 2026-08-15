"""MCP 通知服务端（1.0.2）。

桌宠作为常驻 MCP Server，对 Nanobot / WorkBuddy 等任意 Agent 暴露唯一工具
`notify_task_progress`。来源方作为 MCP Client 在任务生命周期内主动调用，
将执行状态与进展推送到桌面 AI 助理。

传输层：Streamable HTTP（MCP Python SDK），绑定 127.0.0.1（仅本机），
默认端口 18791（与 Nanobot 网关 18790、WebUI 8765 区分）。
鉴权：Bearer Token 中间件（自定义 ASGI 中间件，版本无关、可控）。
依赖：mcp（FastMCP）。若运行环境未安装 mcp，本模块导入会失败，
调用方（aipet.py）应捕获并降级通知功能，不影响桌宠其余功能。

注意：工具回调运行在 MCP 的 asyncio 线程，绝不直接触碰 tkinter；
统一经 app.post_ui 回到主线程再调用 app.bus.emit。
"""

import json
import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP

log = logging.getLogger("notify.mcp")

# 全局持有 DesktopAIPet 实例，供工具回调访问总线。由 aipet.py 启动时 set_app()。
_APP = None


def set_app(app):
    global _APP
    _APP = app


def get_app():
    return _APP


mcp = FastMCP("desktop-aipet-notify")


@mcp.tool()
async def notify_task_progress(
    source: str,
    task_id: str,
    task_name: str,
    status: str,
    event: str = "completed",
    summary: str = "",
    progress: Optional[float] = None,
    detail: str = "",
    url: str = "",
) -> dict:
    """向桌面 AI 助理推送一条任务进展通知。

    Args:
        source: 来源标识，如 "nanobot" / "workbuddy"。
        task_id: 任务在来源内的唯一 ID。
        task_name: 展示名，如「股票买卖点检测」。
        status: 任务状态 pending|running|success|failed|skipped|canceled。
        event: 事件类型 started|progress|completed|error，默认 completed。
        summary: 一句话摘要（建议 ≤200 字）。
        progress: 进度 0~1，可选。
        detail: 详情，可选。
        url: 跳转链接（工作空间 / WebUI），可选。
    """
    from .types import TaskNotification, TaskStatus, TaskEventType

    try:
        n = TaskNotification(
            source=source,
            task_id=task_id,
            task_name=task_name,
            status=TaskStatus(status),
            event=TaskEventType(event),
            summary=summary,
            progress=progress,
            detail=detail,
            url=url,
        )
    except (ValueError, TypeError) as e:
        return {"ok": False, "error": f"参数非法: {e}"}

    app = get_app()
    if app is None:
        return {"ok": False, "error": "通知服务未就绪"}

    # 经 post_ui 回到 tkinter 主线程，再交给总线派发（绝不在 asyncio 线程碰 GUI）
    app.post_ui(lambda: app.bus.emit(n))
    return {"ok": True, "task_id": task_id}


class BearerAuthMiddleware:
    """极简 Bearer Token 鉴权中间件（版本无关，包裹 FastMCP 的 ASGI 应用）。

    仅本机调用场景下，缺失或无效 Authorization 头一律返回 401 JSON。
    """

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            headers = {}
            for k, v in scope.get("headers", []):
                headers[k.decode("latin-1").lower()] = v.decode("latin-1")
            auth = headers.get("authorization", "")
            ok = auth.startswith("Bearer ") and auth[len("Bearer "):].strip() == self.token
            if not ok:
                await self._send_401(send)
                return
        await self.app(scope, receive, send)

    async def _send_401(self, send):
        body = json.dumps(
            {"error": "unauthorized", "message": "缺少或无效的 Bearer Token"}
        ).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})


def build_asgi_app(token: str):
    """构造带 Bearer 鉴权的 Starlette ASGI 应用（streamable-http）。"""
    base = mcp.streamable_http_app()
    return BearerAuthMiddleware(base, token)


def serve(host: str, port: int, token: str):
    """阻塞式启动 MCP Server。**必须在独立 daemon 线程中调用**。"""
    import asyncio
    import uvicorn

    app = build_asgi_app(token)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    log.info("通知 MCP Server 启动于 http://%s:%d/mcp", host, port)
    # 自管事件循环 + server.serve()（不安装信号处理器，规避非主线程限制）
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(server.serve())
