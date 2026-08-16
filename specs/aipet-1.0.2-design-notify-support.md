# 1.0.2 功能规划：工作进展通知机制（MCP-first 修订版）

> 状态：**已实施（2026-08-15）**。v1.0.2 代码已落地并通过端到端冒烟测试（见 `tests/test_notify_smoke.py`）。  
> 实施结论：MCP-first 方向正确，原方案 5 处偏差已在校正版中修正并全部落地。  
> 交付物：`notify/` 包（types/dedup/bus/mcp_server）、`aipet.py` 接入（总线+托盘菜单+设置窗口）、  
> `config.json` 通知配置、Nanobot 桥接脚本 `tools/nanobot_desktop_pet_bridge.py`、打包脚本 `build_installer.bat` 已增补 mcp 依赖收集。

---

## 1. 需求背景

用户每日通过 Nanobot 设定多项定时任务（股票检测、定期备份等），并期望未来接入 WorkBuddy 的工作进展。需要把这些任务的**执行状态与进展**实时同步到桌面 AI 助理，以非阻塞弹窗呈现（任务名、状态、摘要）。

---

## 2. 方案总览（MCP-first）

**桌宠作为常驻的 MCP（Model Context Protocol）Server**，对外暴露一个 `notify_task_progress` 工具；Nanobot / WorkBuddy 等任意工具在其任务完成（或进展更新）时，**作为 MCP client 主动调用**该工具推送通知。

- 桌宠 = 被动接收端（MCP Server），无需轮询、无需解析日志。
- Nanobot / WorkBuddy = 主动推送端（MCP Client），在自身任务生命周期里调用。
- 实时、结构化、标准化（MCP 即契约）、可扩展（新来源零改桌宠）。

---

## 3. 与传统"扫日志"方案对比（确认 MCP 最优）

| 维度  | 日志扫描（兜底）   | **MCP Server（采纳）**               |
| --- | ---------- | -------------------------------- |
| 实时性 | 轮询，≤30s 延迟 | **推送，秒级**                        |
| 结构化 | 正则解析、易碎    | **工具 inputSchema 即契约**           |
| 可扩展 | 每工具写解析器    | **客户端配连接、桌宠零改**                  |
| 标准化 | 否          | **是（Model Context Protocol）**    |
| 安全  | 无          | **Bearer Token + 仅绑定 127.0.0.1** |
| 依赖  | 依赖对方日志格式稳定 | **仅依赖标准 MCP 调用**                 |

**结论**：统一以 MCP 为唯一通知通道，不再保留日志扫描兜底（绝大多数 Agent 均已支持 MCP 调用）。

---

## 4. 架构图

```
┌─────────────┐        MCP (streamable-http)         ┌──────────────────────────┐
│  Nanobot    │ ─── notify_task_progress ───────────▶ │  desktop-aipet (MCP Srv) │
│ (cron 完成) │                                       │  127.0.0.1:18975         │
└─────────────┘                                       │                          │
                                                      │  NotificationMCPServer   │
┌─────────────┐                                       │    │ emit(TaskNotification)│
│  WorkBuddy  │ ─── notify_task_progress ───────────▶ │    ▼                     │
│ (任务完成)  │                                       │  NotificationBus         │
└─────────────┘                                       │    │ dedup + 频次控制     │
                                                      │    ▼                     │
                                            (独立 asyncio daemon 线程)            │
                                                      │    │ post_ui(...)        │
                                                      │    ▼                     │
                                                      │  DesktopAIPet.notify() ──┼──▶ Bubble.show()  ▶ 桌面弹窗
                                                      └──────────────────────────┘
```

---

## 5. 对原规划的偏差校正（重点回答"是否需要调整"）

| # | 原规划（错误）                                       | 真实代码（校正后）                                                                                                         | 影响       |
| - | --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | -------- |
| 1 | `Bubble.show(name, text, kind, duration)`     | `Bubble.show(title, message, level, duration)`，`level` ∈ `info/ok/warn/error`（见 `aipet.py:1046`，`BUBBLE_STYLES`）  | 所有调用签名需改 |
| 2 | MCP 工具回调自行构造 Bubble 调用                        | 已有 `DesktopAIPet.notify(title, message, level)`（1633 行）内部已封装 `post_ui(lambda: bubble.show(...))`——**直接复用即可，少一层**  | 架构简化     |
| 3 | 桌宠 MCP 端口只提 18975                             | Nanobot 网关 health 实为 `127.0.0.1:18790`（`config.json` menus[].Nanobot.endpoint），WebUI 8765。桌宠 MCP 用 **18975** 以示区分 | 端口避免混淆   |
| 4 | 新建 `desktop_aipet.nanobot` 块含 `mode/log_path` | `config.json` 的 `menus[].Nanobot` **已含** `endpoint` / `log_path` / `config_path`，应**复用**而非新建                      | 配置更精简    |
| 5 | 托盘菜单"加一项"未定位                                  | `_build_tray_menu` 的 `more_items`（2219 行，与"🌀 重载配置"并列）是标准增项位置；`ConfigViewer` 已有现成窗口组件可复用                          | 接入点明确    |

**其余原规划内容（MCP 线程 + `post_ui` 回主线程、Bearer Token 鉴权、去重与频次控制、WorkBuddy 经 `mcp.json` 接入）经核对均正确，无需调整。**

---

## 6. 模块划分

新增目录 `notify/`（与现有 `aipet.py` 平级，纯逻辑可单测）：

| 模块                     | 职责                                                                                      |
| ---------------------- | --------------------------------------------------------------------------------------- |
| `notify/types.py`      | `TaskNotification` / `TaskStatus` / `TaskEventType` 数据类与枚举                              |
| `notify/bus.py`        | `NotificationBus`：去重 + 频次控制 + 派发到 `post_ui`                                             |
| `notify/mcp_server.py` | `NotificationMCPServer`：基于 `FastMCP` 起 streamable-http server，暴露 `notify_task_progress` |
| `notify/dedup.py`      | 去重与频次策略（可独立单测）                                                                          |

`aipet.py` 改动：

- `__init__`：读取 `desktop_aipet.notifications` 配置（仅含桌宠侧 MCP server 设置）。
- `run()`：装配并启动 MCP server（daemon 线程）。
- `setup()` 或 `run()`：初始化 `NotificationBus`，绑定到 `self.bus`。
- 通知配置不单独做设置界面，统一在「助理配置」（`ConfigViewer` 打开 `config.json`，可编辑保存后 `_reload_config` 刷新）中调整：`desktop_aipet.notifications.enabled` 为总开关，`menus[].notify` 为按渠道开关（默认开启）。
- 复用现有 `self.notify()` 作为统一弹窗出口。

---

## 7. 数据结构定义

```python
# notify/types.py
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from typing import Optional

class TaskStatus(str, Enum):
    PENDING = "pending"        # 待执行
    RUNNING = "running"        # 执行中
    SUCCESS = "success"        # 成功
    FAILED  = "failed"         # 失败
    SKIPPED = "skipped"        # 跳过
    CANCELED = "canceled"      # 取消

class TaskEventType(str, Enum):
    STARTED = "started"        # 开始
    PROGRESS = "progress"      # 进展
    COMPLETED = "completed"    # 完成
    ERROR = "error"            # 错误

@dataclass
class TaskNotification:
    source: str                # "nanobot" / "workbuddy" / 自定义
    task_id: str               # 任务唯一 ID（来源内唯一即可）
    task_name: str             # 展示名，如 "股票买卖点检测"
    status: TaskStatus
    event: TaskEventType
    summary: str = ""          # 一句话摘要
    progress: Optional[float] = None   # 0~1，可选
    detail: str = ""           # 详情（可点击展开）
    url: str = ""              # 跳转链接（如 WebUI/工作空间）
    timestamp: datetime = field(default_factory=datetime.now)
```

---

## 8. MCP Server 设计

### 8.1 工具定义

```python
# notify/mcp_server.py
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("desktop-aipet-notify")

@mcp.tool()
async def notify_task_progress(
    source: str,
    task_id: str,
    task_name: str,
    status: str,               # "pending"|"running"|"success"|"failed"|"skipped"|"canceled"
    event: str = "completed",  # "started"|"progress"|"completed"|"error"
    summary: str = "",
    progress: float | None = None,
    detail: str = "",
    url: str = "",
) -> dict:
    """推送一条任务进展通知到桌面 AI 助理。"""
    n = TaskNotification(
        source=source, task_id=task_id, task_name=task_name,
        status=TaskStatus(status), event=TaskEventType(event),
        summary=summary, progress=progress, detail=detail, url=url,
    )
    # 关键：回调线程( asyncio )绝不直接碰 tkinter，经 post_ui 回主线程
    app.post_ui(lambda: app.bus.emit(n))
    return {"ok": True}
```

### 8.2 传输层与端口

- 传输：**Streamable HTTP**（MCP Python SDK v1.27.1 已验证支持 `run_streamable_http_async`）。
- 绑定：`127.0.0.1` 仅本机（安全），默认端口 **18975**（与 Nanobot 网关 18790、WebUI 8765 区分）。
- 启动：在 `run()` 里起独立 **daemon 线程**跑 `asyncio` loop；可选延迟 `health_startup_delay_s` 之后启动，避免开机资源争用。

### 8.3 鉴权

- 首次启动随机生成 `token`，持久化到 `config.json`（`desktop_aipet.notifications.mcp.token`）。
- Streamable HTTP 中间件统一校验 `Authorization: Bearer <token>`，失败返回 401。
- 客户端（Nanobot/WorkBuddy）需配置该 token。

---

## 9. 通知触发流程

```
Nanobot/WorkBuddy 任务完成
   │  (MCP client) 调用 notify_task_progress(...)
   ▼
NotificationMCPServer.notify_task_progress  (asyncio 线程)
   │  app.post_ui(lambda: app.bus.emit(n))
   ▼
NotificationBus.emit(n)  (tkinter 主线程，经 post_ui 回到)
   │  1) dedup.should_notify(n)  → 去重 + 频次判定
   │  2) 通过 → 构造 (title, message, level)
   ▼
DesktopAIPet.notify(title, message, level)   # 复用现有统一入口
   │  post_ui(lambda: bubble.show(title, message, level))
   ▼
Bubble.show(...)  → 桌面非阻塞弹窗
```

**失败/离线路径**：MCP client 调用失败不影响来源侧任务；桌宠侧 server 进程内常驻，无需担心"拉取不到"。

---

## 10. 去重与频次控制

复用现有 `last_notify_time` 字典（见 `run_health_check` 1614 行附近）的模式，扩展为 `NotificationBus`：

- **按身份去重**：key = `source:task_id:日期`。同一任务同日已通知过"完成"则抑制重复。
- **同状态抑制**：收到 `success` 后再收 `success` 不重复弹（除非间隔 > N 分钟）。
- **频次上限**：默认每分钟最多 3 条，超出则合并为"N 条新进展"汇总；`FAILED`/`ERROR` 类豁免（重要异常必弹）。
- **同批合并**：1 秒内多条同类通知合并为一条汇总。
- **用户可控**：通知开关统一在「助理配置」（`config.json`）中修改——`desktop_aipet.notifications.enabled` 总开关、`menus[].notify` 按渠道开关（默认开启，删去或置 `false` 即关闭）；保存后由 `_reload_config` 实时刷新，无需独立设置窗口。

---

## 11. 桌面弹窗交互设计

**复用现有 `Bubble` 机制（非阻塞，自动消失）**，经 `app.notify()` 统一出口：

- **布局**：紧贴宠物显示（上方优先，空间不足落下方），多任务时**最多同屏 3 条**排队，旧的自然过期后补位。
- **内容**：标题=任务名（如"股票买卖点检测"），正文=状态徽标 + 摘要（如"✅ 已完成：触发卖出信号"）。
- **状态映射**：`success→ok`、`failed/error→error`、`running→info`、`skipped→warn`。
- **交互**：hover 暂停自动消失；点击跳转 `url`（工作空间/WebUI）；右上角"×"手动关闭。
- **零阻塞**：`Bubble.show` 在 `Toplevel` 上渲染，`_pump_ui` 每 50ms 消费队列，不卡主线程。

---

## 12. 配置变更（config.json 真实片段）

**设计原则：通知开关并入各来源已有配置，不再单独建 `sources` 字典。**

桌宠侧 `config.json` 只保留**自身 MCP server 的运行参数**；每个来源（Nanobot / WorkBuddy）的"是否启用通知"开关，直接放进它们**各自已有的配置块**：

- Nanobot → 桌宠 `menus[].Nanobot` 块（本就存在 `endpoint`/`log_path`/`actions`）新增 `"notify": true`；
- WorkBuddy → 其自身 MCP 配置中（见 §13.2）；
- 实际的 MCP **客户端连接**（url + token）写在 **Nanobot / WorkBuddy 各自的配置文件**里（§13），桌宠不重复持有。

```json
{
  "desktop_aipet": {
    "health_startup_delay_s": 60,
    "notifications": {
      "enabled": true,
      "mcp": {
        "enabled": true,
        "host": "127.0.0.1",
        "port": 18975,
        "token": "<首次启动随机生成并回填>"
      },
      "max_per_minute": 3,
      "dedup_minutes": 5
    }
  },
  "menus": [
    {
      "name": "Nanobot",
      "endpoint": "http://127.0.0.1:18790/health",
      "log_path": "%USERPROFILE%\\.nanobot\\logs\\gateway.log",
      "notify": true,
      "actions": [ ... ]
    }
  ]
}
```

> 注：`endpoints`(18790) 是 Nanobot 网关 health；桌宠 MCP `port`(18975) 是供 Nanobot/WorkBuddy **反向调用**的端口，二者区分。来源启用开关 `notify` 直接挂在已有菜单块上，不另起 `sources`。

桌宠侧读取逻辑：`app.notify_enabled = desktop_aipet.notifications.enabled and any(menu.get("notify") for menu in menus if menu["name"]=="Nanobot")`；WorkBuddy 侧同理（其能力由 WorkBuddy 自身配置决定是否连接）。

---

## 13. 各来源接入指南（Nanobot / WorkBuddy，均零改桌宠）

> 桌宠只暴露 MCP server；"谁来调、何时调"由来源方自己配置。下面给出**可落地的真实配置**（已对照 Nanobot v0.3.0 源码核实）。

### 13.1 Nanobot 如何配置该 MCP（~/.nanobot/config.json）

Nanobot v0.3.0 **原生支持 `tools.mcpServers`**（`mcp.py` 已内置 `streamable_http_client`）。在 `config.json` 的 `tools` 块下注册桌宠 server 即可——**无需改任何 Nanobot 代码**：

```json
{
  "tools": {
    "mcpServers": {
      "desktop-aipet": {
        "type": "streamableHttp",
        "url": "http://127.0.0.1:18975/mcp",
        "headers": { "Authorization": "Bearer <桌面宠物生成的token>" },
        "enabled_tools": ["*"]
      }
    }
  }
}
```

要点（已对照 `nanobot/agent/tools/mcp.py` 核实）：

- `type` 可省略，Nanobot 会按 `url` 自动判定：`url` 不以 `/sse` 结尾 → `streamableHttp`。显式写更稳。
- 工具名会被包装为 **`mcp_{server名}_{tool名}`**，即本例注册名 `desktop-aipet` + 工具 `notify_task_progress` → 实际可调工具为 **`mcp_desktop_aipet_notify_task_progress`**（`-` 等会被 sanitize）。
- 改完**重启 Nanobot 网关**生效；可在网关日志/工具列表中确认 `desktop-aipet` 已连接、工具已加载。
- `headers` 里的 token 与桌宠 `notifications.mcp.token` 一致（桌宠首次启动随机生成并回填到自己的 config.json，你把它复制进这里）。

### 13.2 如何让"任务执行时"确定性通知桌宠（hooks 设置）

Nanobot v0.3.0 **没有 config/entry-point 驱动的 hook 注册面**：`AgentHook` 只能以代码方式注入；cron 的 `on_job` 回调在 CLI 里实例化 `CronService(cron_store_path)` 时**未**被接通。因此"任务完成自动通知"有两条现实路径，按稳健程度推荐：

#### 方式一（推荐先用，零代码，利用 MCP 原生能力）

把"结束时推送通知"写进 **agent 默认指令 / 每个 cron job 的 prompt**，例如：

> 任务完成后，调用工具 `mcp_desktop_aipet_notify_task_progress` 向桌面 AI 助理推送一条通知，参数：source="nanobot"、task_name=本任务名、status=success/failed、summary=本次执行结论（≤200字）。

因为工具已通过 §13.1 注册，LLM 会自主调用并**自然携带最终结论作为摘要**。优点：零改动、跨版本稳定；缺点：依赖 LLM 遵从（极少数情况可能漏调，可把指令写进 agent 系统提示而非仅靠单次 prompt 来强化）。

#### 方式二（确定性，轻量包装脚本，不改 Nanobot 核心）

若需"无论 LLM 是否记得都必通知"，用一个独立桥接脚本在启动 Nanobot 前 monkeypatch 注入 hook（不改动 Nanobot 安装目录，升级 Nanobot 也不丢）。二选一：

**(a) 仅"任务开始"确定性 ping**——注入 cron `on_job`（在 `_execute_job` 执行前触发）：

```python
# nanobot_desktop_pet_bridge.py  （启动 nanobot 前先 import 本模块）
import nanobot.cron.service as cron_mod
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession

URL = "http://127.0.0.1:18975/mcp"; TOKEN = "<token>"

async def _on_job(job):
    async with streamablehttp_client(URL) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            await s.call_tool("notify_task_progress", {
                "source": "nanobot", "task_id": job.id, "task_name": job.name,
                "status": "running", "event": "started",
                "summary": f"定时任务 {job.name} 开始执行"})

_orig = cron_mod.CronService.__init__
def _patched(self, *a, **k):
    k.setdefault("on_job", _on_job); _orig(self, *a, **k)
cron_mod.CronService.__init__ = _patched
```

**(b) "完成 + 摘要"确定性通知**——注入 `AgentHook.after_run`（能拿到 `final_content`/`error`）：

```python
# nanobot_desktop_pet_bridge.py
import nanobot.agent.loop as loop_mod
from nanobot.agent.hook import AgentHook
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession

URL = "http://127.0.0.1:18975/mcp"; TOKEN = "<token>"

async def _notify(status, name, summary):
    async with streamablehttp_client(URL) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            await s.call_tool("notify_task_progress", {
                "source": "nanobot", "task_id": "run", "task_name": name,
                "status": status, "event": "completed", "summary": summary[:200],
                "url": "http://127.0.0.1:8765"})

class DesktopPetNotifyHook(AgentHook):
    async def after_run(self, ctx):
        status = "failed" if ctx.error else "success"
        summary = (ctx.error or (ctx.final_content or ""))
        await _notify(status, "Nanobot 任务", summary)

_orig = loop_mod.AgentLoop.__init__
def _patched(self, *a, **k):
    hooks = list(k.get("hooks") or []); hooks.append(DesktopPetNotifyHook())
    k["hooks"] = hooks; _orig(self, *a, **k)
loop_mod.AgentLoop.__init__ = _patched
```

启动方式：写个 `run_nanobot.py` 先 `import nanobot_desktop_pet_bridge` 再调用 Nanobot CLI；或 `python -c "import nanobot_desktop_pet_bridge, nanobot"` 形式启动。桥接脚本与 Nanobot 安装目录分离，升级 Nanobot 不受影响。

> 两种 hook 方式都会走 MCP（与方式一同一通道），桌宠侧**完全零改动**，只是把"何时调"从"靠 LLM 记得"变成"代码必调"。

> 上述桥接脚本已落盘为 **`tools/nanobot_desktop_pet_bridge.py`**：按文件头注释把 `DESKTOP_PET_MCP_URL` / `DESKTOP_PET_TOKEN` 改为你 `config.json` 中 `desktop_aipet.notifications.mcp` 的实际值，再用 `python -c "import nanobot_desktop_pet_bridge, nanobot"` 作为 Nanobot 启动入口即可（方式二-b 默认启用；方式二-a 见脚本内 `_patch_cron_on_job`）。

### 13.3 WorkBuddy 接入（未来，零改 WorkBuddy 代码）

在 WorkBuddy 的 MCP 配置（如 `~/.workbuddy/mcp.json`）加一项 server——这正是 WorkBuddy **已有**的 MCP 配置位置：

WorkBuddy agent 即可在任务完成时自主调用 notify_task_progress。这正是 MCP 范式的精髓：来源方在各自已有配置里加一条连接、桌宠零改动；新增任何支持 MCP 的 Agent 都无需桌宠配合。

````json
```json
{
  "mcpServers": {
    "desktop-aipet": {
      "url": "http://127.0.0.1:18975/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```
````

---

## 14. 实施里程碑

| 阶段 | 内容                                                                                | 可验证                                                |
| -- | --------------------------------------------------------------------------------- | -------------------------------------------------- |
| M0 | `notify/types.py` 数据类                                                             | 单测构造                                               |
| M1 | `notify/bus.py` + `dedup.py`（去重/频次）                                               | 单测：同任务重复抑制、频次上限                                    |
| M2 | `notify/mcp_server.py`（FastMCP + 鉴权 + 独立线程）                                       | 用 MCP Inspector 或 curl 调 `notify_task_progress` 成功 |
| M3 | `aipet.py` 装配：`run()` 启动 server、`app.bus` 接线 `app.notify`                         | 实跑：收到通知弹窗                                          |
| M4 | 通知配置统一在「助理配置」（`ConfigViewer` 打开 `config.json`）中修改，无独立设置界面                         | 编辑保存后 `_reload_config` 刷新开关生效                      |
| M5 | Nanobot 侧接入验证：`~/.nanobot/config.json` 注册 `desktop-aipet` MCP server + 方式一/二 hook | Nanobot 定时任务完成时桌宠弹窗                                |
| M6 | 版本号 → 1.0.2，构建 exe/zip 发布                                                         | Release 含两形态                                       |

---

## 15. 真实代码校验点（实施前必读）

已在本文第 5 节完成对照。关键真值：

- `Bubble.show(title, message, level, duration)` — `aipet.py:1046`
- `DesktopAIPet.notify(title, message, level)` — `aipet.py:1633`
- `post_ui(fn)` + `_pump_ui`（50ms 队列轮询）— `aipet.py:1614` / `1617`
- `run_health_check` 去重范式 `last_notify_time` + 阈值 — `aipet.py:2461`
- 托盘菜单增项位置 `more_items` — `aipet.py:2219`
- Nanobot 配置字段 `endpoint`/`log_path` 已存在于 `config.json` menus

---

## 16. 风险与待确认

1. **MCP SDK 运行时依赖**：`mcp` 包需随 PyInstaller 打包（已确认 v1.27.1 装在 `D:\Software\Python3`；打包需 `--collect-all mcp` 及 httpx/sse 相关）。
2. **端口冲突**：18975 若被占用需可配置回退。
3. **鉴权 token 持久化**：首次生成后写入 `config.json`（`desktop_aipet.notifications.mcp.token`），重装/迁移需重新授权；Nanobot/WorkBuddy 侧需手动复制同一 token。
4. **Nanobot hook 注入方式**：v0.3.0 无 config/entry-point 驱动的 hook 注册面，`AgentHook` 仅能代码注入；推荐优先用"方式一（prompt 引导）"零代码，必要时用"方式二"独立桥接脚本 monkeypatch（与 Nanobot 安装目录分离，升级不丢）。
5. **WorkBuddy mcp.json 字段名**：以 WorkBuddy 实际 schema 为准，本文示例为通用形态，连接信息（url+token）同样写在其已有 MCP 配置内。
