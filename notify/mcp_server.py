"""MCP 通知服务端（1.0.2）。

桌宠作为常驻 MCP Server，对 Nanobot / WorkBuddy 等任意 Agent 暴露唯一工具
`notify_task_progress`。来源方作为 MCP Client 在任务生命周期内主动调用，
将执行状态与进展推送到桌面 AI 助理。

传输层：Streamable HTTP（MCP Python SDK），绑定 127.0.0.1（仅本机），
默认端口 18975（与 Nanobot 网关 18790、WebUI 8765 区分）。
鉴权：Bearer Token 中间件（自定义 ASGI 中间件，版本无关、可控）。
依赖：mcp（FastMCP）。若运行环境未安装 mcp，本模块导入会失败，
调用方（aipet.py）应捕获并降级通知功能，不影响桌宠其余功能。

健壮性（针对 Windows 端口占用）：
- 启动前探测可用端口（带 SO_REUSEADDR，可识别 TIME_WAIT 复用）；
- 绑定期间让 uvicorn 监听 socket 自动带 SO_REUSEADDR，规避 Windows 上
  TIME_WAIT 导致的 10048（地址已在使用，但 netstat 看不到监听者）；
- 默认端口被占用时自动 +1 漂移，并通过 on_port 回调告知实际端口。
每次 serve 创建新的 FastMCP 实例，支持重复启动（如未来热重载）。

注意：工具回调运行在 MCP 的 asyncio 线程，绝不直接触碰 tkinter；
统一经 app.post_ui 回到主线程再调用 app.bus.emit。
"""

import contextlib
import contextvars
import json
import logging
import socket as _socket
from typing import Optional

from mcp.server.fastmcp import FastMCP

log = logging.getLogger("notify.mcp")

# 当前请求的来源 IP：由鉴权中间件在请求上下文写入，供工具回调（notify_task_progress）
# 在运行日志中溯源记录调用方来源。
_CLIENT_IP = contextvars.ContextVar("client_ip", default="unknown")

# 全局持有 DesktopAIPet 实例，供工具回调访问总线。由 aipet.py 启动时 set_app()。
_APP = None


def set_app(app):
    global _APP
    _APP = app


def get_app():
    return _APP


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

    # 记录通知来源（source）与调用方 IP，便于在运行日志中溯源
    log.info(
        "收到任务进展通知 source=%s client_ip=%s task_id=%s task_name=%s "
        "status=%s event=%s",
        source, _CLIENT_IP.get(), task_id, task_name, status, event)

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
            client = scope.get("client")
            client_ip = client[0] if isinstance(client, (tuple, list)) and client else "unknown"
            # 健康检查端点：无需鉴权（仅本机 127.0.0.1 调用），供桌宠自身健康检测使用
            if scope.get("path", "/") == "/health":
                await self._send_200(send)
                return
            headers = {}
            for k, v in scope.get("headers", []):
                headers[k.decode("latin-1").lower()] = v.decode("latin-1")
            auth = headers.get("authorization", "")
            ok = auth.startswith("Bearer ") and auth[len("Bearer "):].strip() == self.token
            if not ok:
                log.warning("MCP 鉴权失败 client_ip=%s path=%s", client_ip, scope.get("path"))
                await self._send_401(send)
                return
            # 鉴权通过：记录来源 IP，供工具回调（notify_task_progress）在日志中溯源
            _CLIENT_IP.set(client_ip)
        await self.app(scope, receive, send)

    async def _send_200(self, send):
        body = json.dumps({"status": "ok"}).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})

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
    """构造带 Bearer 鉴权的 Starlette ASGI 应用（streamable-http）。

    每次调用创建新的 FastMCP 实例并注册工具，因此可重复 serve
    （规避 MCP SDK「SessionManager 只能 run 一次」的限制）。
    """
    mcp = FastMCP("desktop-aipet-notify")
    mcp.tool()(notify_task_progress)
    base = mcp.streamable_http_app()
    return BearerAuthMiddleware(base, token)


@contextlib.contextmanager
def _reuse_addr_scope():
    """在作用域内让所有 socket.bind 自动设置 SO_REUSEADDR。

    Windows 上 uvicorn/anyio 默认不为监听 socket 设置 SO_REUSEADDR，
    桌宠重启时若旧端口仍在 TIME_WAIT 会触发 10048（地址已在使用）。
    此作用域在绑定期间临时补上该选项，作用域结束即还原，影响面最小。
    """
    orig = _socket.socket.bind

    def _bind_with_reuse(self, *args, **kwargs):
        try:
            self.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        except Exception:
            pass
        return orig(self, *args, **kwargs)

    _socket.socket.bind = _bind_with_reuse
    try:
        yield
    finally:
        _socket.socket.bind = orig


def _probe_free_port(host: str, start_port: int, max_tries: int = 20):
    """探测可用端口：依次尝试 start_port .. start_port+max_tries-1。

    使用带 SO_REUSEADDR 的临时 socket 做 bind+close 测试，因此能识别
    可被 TIME_WAIT 复用的端口，避免把"假占用"误判为不可用。
    返回首个可用端口；全部不可用返回 None。
    """
    for i in range(max_tries):
        p = start_port + i
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, p))
            s.close()
            return p
        except OSError:
            try:
                s.close()
            except Exception:
                pass
    return None


def serve(host: str, port: int, token: str, max_tries: int = 20, on_port=None):
    """阻塞式启动 MCP Server。**必须在独立 daemon 线程中调用**。

    健壮性处理：
    - 启动前探测可用端口（带 SO_REUSEADDR，可识别 TIME_WAIT 复用）；
    - 绑定期间通过 `_reuse_addr_scope` 让 uvicorn 监听 socket 自动带
      SO_REUSEADDR，规避 Windows 上 TIME_WAIT 导致的 10048；
    - 默认端口被占用时自动 +1 漂移；选定端口通过 `on_port` 回调回传
      （供调用方写回配置 / 提示用户同步来源端配置）。
    """
    import asyncio
    import uvicorn

    chosen = _probe_free_port(host, port, max_tries)
    if chosen is None:
        raise OSError(
            f"无法在 {host}:{port} 起的 {max_tries} 个端口内找到可用端口")
    if on_port:
        try:
            on_port(chosen)
        except Exception:
            log.warning("on_port 回调异常（忽略）", exc_info=True)

    app = build_asgi_app(token)
    with _reuse_addr_scope():
        # pythonw 下 sys.stdout 为 None，uvicorn 默认 logging 配置会调
        # sys.stdout.isatty() 触发 AttributeError；禁用其自带 log_config，
        # 复用 aipet.py 已配置好的 logging，同时关闭 access_log 避免多余输出。
        config = uvicorn.Config(
            app, host=host, port=chosen, log_level="warning",
            log_config=None, access_log=False)
        server = uvicorn.Server(config)
        log.info("通知 MCP Server 启动于 http://%s:%d/mcp", host, chosen)
        # 自管事件循环 + server.serve()（不安装信号处理器，规避非主线程限制）
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve())
