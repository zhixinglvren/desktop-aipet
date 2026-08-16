"""Nanobot → 桌面AI助理 通知桥接脚本（1.0.2）。

用途：让 Nanobot v0.3.0 在任务执行/完成时，确定性地调用桌宠暴露的 MCP 工具
`notify_task_progress`，把进展推送到桌面 AI 助理。

为什么需要它（见规划 §13.2）：Nanobot v0.3.0 没有 config/entry-point 驱动的
hook 注册面，AgentHook 只能以代码方式注入。本脚本在**启动 Nanobot 之前**
monkeypatch 注入 hook，**不改动 Nanobot 安装目录**，升级 Nanobot 不受影响。

两种注入（按稳健程度任选其一，或都保留）：
  (a) cron `on_job`  —— 任务「开始」确定性 ping（方式二-a）
  (b) AgentHook.after_run —— 「完成 + 摘要」确定性通知（方式二-b）

使用：
  1) 先启动桌宠（它会起 http://127.0.0.1:18975/mcp 的 MCP Server）。
  2) 把下面 URL / TOKEN 改成你 config.json 里
     desktop_aipet.notifications.mcp 的实际值（TOKEN 即桌宠首次启动随机生成
     并回填到它自己 config.json 的那串；复制到这里）。
  3) 用本模块替换 Nanobot 的启动入口，例如：
       python -c "import nanobot_desktop_pet_bridge, nanobot"
     或在你的 run_nanobot.py 顶部 `import nanobot_desktop_pet_bridge` 后再启动 Nanobot。

更轻量（零代码）的方案是规划 §13.1 + §13.2 方式一：仅在 agent 指令里要求
任务结束时调用 `mcp_desktop_aipet_notify_task_progress`，无需本脚本。
"""

import asyncio

# ---- 连接配置：改成你桌宠 config.json 里的实际值 ----
DESKTOP_PET_MCP_URL = "http://127.0.0.1:18975/mcp"
DESKTOP_PET_TOKEN = "<在此粘贴桌宠 config.json 的 notifications.mcp.token>"


async def _call_notify(payload: dict):
    """经 Streamable HTTP 调用桌宠 MCP 工具的 notify_task_progress。"""
    from mcp.client.streamable_http import streamablehttp_client
    from mcp import ClientSession
    async with streamablehttp_client(
        DESKTOP_PET_MCP_URL,
        headers={"Authorization": f"Bearer {DESKTOP_PET_TOKEN}"},
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool("notify_task_progress", payload)


# ---------------------------------------------------------------------------
# 方式二-b：AgentHook.after_run —— 任务「完成」时携带最终结论通知桌宠
# ---------------------------------------------------------------------------
def _patch_agent_hook():
    try:
        import nanobot.agent.loop as loop_mod
        from nanobot.agent.hook import AgentHook
    except Exception as e:  # pragma: no cover
        print(f"[bridge] 未找到 nanobot.agent.hook，跳过 after_run 注入: {e}")
        return

    async def _notify(status: str, name: str, summary: str):
        try:
            await _call_notify({
                "source": "nanobot",
                "task_id": "run",
                "task_name": name,
                "status": status,
                "event": "completed",
                "summary": (summary or "")[:200],
                "url": "http://127.0.0.1:8765",
            })
        except Exception as e:
            print(f"[bridge] 通知桌宠失败: {e}")

    class DesktopPetNotifyHook(AgentHook):
        async def after_run(self, ctx):
            status = "failed" if getattr(ctx, "error", None) else "success"
            summary = getattr(ctx, "error", None) or (getattr(ctx, "final_content", None) or "")
            await _notify(status, "Nanobot 任务", summary)

    _orig = loop_mod.AgentLoop.__init__

    def _patched(self, *a, **k):
        hooks = list(k.get("hooks") or [])
        hooks.append(DesktopPetNotifyHook())
        k["hooks"] = hooks
        _orig(self, *a, **k)

    loop_mod.AgentLoop.__init__ = _patched
    print("[bridge] 已注入 DesktopPetNotifyHook(after_run)")


# ---------------------------------------------------------------------------
# 方式二-a：cron on_job —— 任务「开始」确定性 ping（可选）
# ---------------------------------------------------------------------------
def _patch_cron_on_job():
    try:
        import nanobot.cron.service as cron_mod
    except Exception as e:  # pragma: no cover
        print(f"[bridge] 未找到 nanobot.cron.service，跳过 on_job 注入: {e}")
        return

    async def _on_job(job):
        try:
            await _call_notify({
                "source": "nanobot",
                "task_id": getattr(job, "id", "cron"),
                "task_name": getattr(job, "name", "定时任务"),
                "status": "running",
                "event": "started",
                "summary": f"定时任务 {getattr(job, 'name', '')} 开始执行",
            })
        except Exception as e:
            print(f"[bridge] 通知桌宠失败(on_job): {e}")

    _orig = cron_mod.CronService.__init__

    def _patched(self, *a, **k):
        k.setdefault("on_job", _on_job)
        _orig(self, *a, **k)

    cron_mod.CronService.__init__ = _patched
    print("[bridge] 已注入 cron on_job 钩子")


_patch_agent_hook()
_patch_cron_on_job()
print("[bridge] 桌宠通知桥接已就绪（Nanobot 启动后将自动通知桌宠）")
