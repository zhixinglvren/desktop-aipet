# Desktop AIPet — 桌面 AI 助理 设计说明书

> 版本：v1.0（配置驱动型）
> 更新日期：2026-08-06
> 作者：wangxin49245

---

## 1. 概述

### 1.1 项目定位

Desktop AIPet 是一个轻量级 Windows 桌面 AI 助理。它以**系统托盘图标 + 可拖拽悬浮桌宠**双形态常驻桌面，通过 HTTP 健康探测监控本地 AI 工具（当前为 Nanobot Gateway）的运行状态，并通过右键菜单提供对 Gateway 的启停、WebUI、工作空间跳转、渠道授权、配置与日志查看。

**所有菜单行为均由 `config.json` 驱动，不在代码中硬编码。** 修改/新增菜单动作只需编辑配置，无需改动 `aipet.py`（例外情况见 aipet-spec.md 硬约束）。

### 1.2 双形态设计

| 形态 | 技术 | 说明 |
|------|------|------|
| 📌 系统托盘图标 | `pystray` | 通知区域常驻；右键菜单为完整控制入口；鼠标悬停提示仅显示"桌面AI助理" |
| 🖼️ 桌面悬浮桌宠 | `tkinter` | frameless + 置顶 + 透明背景 + 可拖拽；旁侧附带对话式气泡 `PetBubble` 展示当前活动/操作反馈；与托盘共享同一套健康状态与右键菜单 |

### 1.3 核心价值

- **状态可见**：托盘/桌宠右键菜单标题与子菜单项的 emoji（🟢/🟡/🔴）一眼知状态，不点开菜单亦可判断服务健康。
- **不碍事**：悬浮宠物可拖拽到角落、可隐藏、可关闭桌面显示（仅留托盘）。
- **配置化**：菜单动作、监控目标、路径全部走 `config.json`，扩展零代码。
- **离线可靠**：应用内对话式气泡 `PetBubble`（tkinter，依附桌宠）替代传统弹出式 Toast，适配离线金融内网。

### 1.4 项目路径

```
E:\Portfolio\desktop-aipet\
```

---

## 2. 技术栈

| 组件 | 选型 | 理由 |
|------|------|------|
| 桌面悬浮窗 | `tkinter`（Python 内置） | 零额外安装；frameless + 透明色 + 置顶 |
| 托盘图标 | `pystray` | 纯 Python，稳定；支持动态重建菜单 |
| 宠物/托盘图标绘制 | `Pillow`（PIL） | 程序化生成 PNG/ICO，支持透明通道与中性机器人形象 |
| HTTP 健康探测 | `httpx`（缺失时回退 `urllib`） | 复用环境已有依赖 |
| 应用内对话气泡 | `tkinter.Toplevel`（`PetBubble`） | 依附桌宠展示当前活动/操作反馈，不依赖系统通知 API，离线环境可靠 |
| 开机启动 | `start.vbs` + Startup 目录 | 标准做法；`pythonw` 无控制台启动 |

---

## 3. 技术架构

```mermaid
graph TB
    subgraph UI[Tk 主线程 · UI 层]
        Pet[桌面悬浮宠物窗口]
        Tray[系统托盘图标 pystray]
        Pop[配置/日志弹窗 Toplevel]
        Bubble[桌宠对话气泡 PetBubble]
    end
    subgraph Core[Tk 主线程 · 核心逻辑]
        App[DesktopAIPet]
        Pump[_pump_ui 50ms 轮询 _ui_queue]
        Exec[_execute_action 到 _do_type]
    end
    subgraph BG[后台线程]
        Probe[_probe_worker 健康检查 httpx]
    end
    Probe -->|状态结果| App
    App -->|update_tray_icon| Tray
    App -->|update_pet_state| Pet
    Tray -->|菜单回调 post_ui| Pump
    Pet -->|右键 post_ui| Pump
    Pump --> Exec
    Exec --> Pop
    Exec --> Bubble
    Exec -->|os.startfile / launch| Ext[外部进程 .bat .ps1 浏览器]
```

**关键约束**：`pystray.Icon.run()` 运行在**托盘线程**，所有 `tkinter` 操作必须经由 `post_ui()` 编组到 Tk 主线程（`_pump_ui` 每 50ms 轮询 `_ui_queue` 执行）。跨线程直接操作 tkinter 会被 `pythonw` 静默吞掉异常，表现为"菜单点击无反应"。详见 aipet-spec.md §3。

---

## 4. 目录结构

```
desktop-aipet/
├── aipet.py              # 主程序入口（全部逻辑，约 1400 行）
├── config.json           # 监控与菜单配置（核心可配置项）
├── aipet-design.md       # 本设计说明书
├── aipet-spec.md         # SDD 硬约束（AI 编码代理必须遵守）
├── pets/
│   └── robot/            # 桌宠形象 PNG（首次启动由 PIL 生成并缓存）
│       ├── normal_0.png / normal_1.png
│       ├── warning_0.png / warning_1.png
│       └── error_0.png / error_1.png
├── icons/                # 托盘 .ico（启动时由 PIL 生成并落盘）
│   ├── normal.ico
│   ├── warning.ico
│   └── error.ico
├── logs/
│   └── aipet.log         # 运行日志（UTF-8，RotatingFileHandler，2MB 轮转×3）
└── start.vbs             # 开机静默启动脚本源
```

> 注：旧版文档中的 `pet.py`、`pets/cat|bunny` 等多主题目录已不再使用。当前仅 `robot` 主题，形象由代码程序化生成，无需手工图片资源。

---

## 5. 配置设计（config.json）

### 5.1 顶层结构

```json
{
  "desktop_aipet": { /* 桌宠与全局开关 */ },
  "monitors": [
    {
      "name": "Nanobot",
      "endpoint": "http://127.0.0.1:18790/health",
      "enabled": true,
      "config_path": "%USERPROFILE%\\.nanobot\\config.json",
      "log_path": "%USERPROFILE%\\.nanobot\\logs\\gateway.log",
      "workspace_key": "agents.defaults.workspace",
      "actions": [ /* 菜单动作数组，顺序即菜单显示顺序 */ ]
    }
  ]
}
```

### 5.2 `desktop_aipet` 字段

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `pet_theme` | `"robot"` | 桌宠主题（当前仅 robot 内置） |
| `pet_visible` | `true` | 是否创建悬浮宠物窗口；`false` 时仅托盘 |
| `pet_scale` | `1.0` | 缩放比例（适配高分屏） |
| `pet_x` / `pet_y` | 运行时保存 | 悬浮宠物退出时的坐标；`null` 时默认右下角 |
| `check_interval_s` | `30` | 健康检测间隔（秒） |
| `log_retention_days` | `7` | 日志保留天数（参考值） |
| `auto_restart_on_failure` | `false` | 失败自启开关（未强制启用） |

### 5.3 `monitor` 字段

| 字段 | 说明 |
|------|------|
| `name` | 监控项名称，作为菜单分组标题 |
| `endpoint` | 健康探测 URL（HTTP GET） |
| `enabled` | 是否启用该监控项 |
| `config_path` | 该服务的配置文件路径，供 `popup_file` / `open_workspace` 复用 |
| `log_path` | 该服务运行时日志路径，供 `popup_log` 复用 |
| `workspace_key` | 工作空间字段在配置文件中的 dotted 路径（如 `agents.defaults.workspace`） |
| `actions[]` | 该监控项下的右键菜单动作；**数组顺序即菜单显示顺序** |

所有路径支持 `%USERPROFILE%`、`~` 展开（经 `expand()`），不写死绝对路径。

### 5.4 动作类型总表

每个动作是 JSON 对象，必须显式声明 `type`。解析链：`_action_type()` → `_execute_action()` → `_do_<type>()`。

| `type` | 用途 | 关键字段 |
|--------|------|----------|
| `url` | 打开浏览器/地址 | `url` |
| `file` | 用默认程序打开文件 | `path` / `file` |
| `cmd` | 执行 cmd 命令 | `command`/`cmd`、`window`(`visible`/`hidden`)、`keep_open` |
| `powershell` | 执行 PowerShell 命令 | `command`、`window`、`keep_open` |
| `script` | 执行 `.bat`/`.ps1` 脚本 | `path`、`window`、`tee_log`、`log_path`、`append_log`、`keep_open` |
| `script_seq` | 顺序执行多步（如 停止→启动） | `steps[]`、`delay_s`（步间延迟秒） |
| `open_workspace` | 读取配置 workspace 字段并打开文件夹 | `config_path`、`config_key`（或 monitor 级 `workspace_key`） |
| `popup_file` | 只读文件查看器（JSON 自动格式化） | `path`、`title`、`pretty_json` |
| `popup_log` | 实时尾随日志窗口 | `path`/`log_path`、`refresh_ms`、`tail_lines`、`title`、`hint` |

新增类型只需实现 `_do_<type>` 方法，分发逻辑（`getattr`）无需改动。

### 5.5 当前实际菜单（Nanobot 监控项）

顺序与行为：

1. **启动 Gateway**（`script`）— 可见窗口执行 `start-gateway.bat`，同时 tee 输出到 `gateway.log`
2. **重启 Gateway**（`script_seq`）— 先 `stop-gateway.bat`（隐藏、等待完成），间隔 3 秒后执行 `start-gateway.bat`（可见 + tee）
3. **打开 WebUI**（`url`）— 打开 `http://127.0.0.1:8765`
4. **跳转工作空间**（`open_workspace`）— 读取 nanobot 配置 `agents.defaults.workspace` 并打开该文件夹
5. **微信渠道授权**（`powershell`）— 可见 PowerShell 执行 `nanobot channels login weixin --force`，展示微信二维码
6. **查看运行配置**（`popup_file`）— 只读窗口展示 `%USERPROFILE%\.nanobot\config.json`（JSON 自动格式化）
7. **查看运行日志**（`popup_log`）— 实时尾随 `gateway.log`，支持关键字过滤、自动滚动、颜色高亮、定时刷新

---

## 6. 状态系统

| 状态 | 颜色 | 含义 | 圆点 emoji |
|------|------|------|------------|
| `normal` | `#22c55e`（绿） | 所有监控项正常 | 🟢 |
| `warning` | `#f59e0b`（黄） | 部分异常（有正常有异常） | 🟡 |
| `error` | `#ef4444`（红） | 全部异常 | 🔴 |

**表达位置**：

- **机器人图标 / 桌宠**：保持**中性配色**（灰蓝身体、青色屏幕、黄色天线），不随状态变色，也不叠加任何状态圆点或徽标。
- **顶层菜单标题**：固定显示 "🟢/🟡/🔴 桌面AI助理"，使用**聚合状态**（见下）。
- **各监控项子菜单标题**：显示 "🟢 MonitorName" 或 "🔴 MonitorName"，**仅二态**（正常=绿，异常=红），不显示 "HTTP 200" 等探测详情。

**文字规则**：菜单标题仅写 "桌面AI助理" 或监控名，不写 "全部正常"/"异常"/"HTTP 200" 等摘要文字；鼠标悬停提示（tooltip）仅保留 "桌面AI助理"。

**聚合规则**：所有启用 `monitor` 健康结果 → 全正常 `normal`/`🟢`；有正常有异常 `warning`/`🟡`；全异常 `error`/`🔴`。`warning` 只出现在聚合层，单个监控项没有黄色状态。

**更新规则**：状态变化时调用 `update_tray_icon(state, changed=True)` 同时换图标 + `update_menu()`；平时仅更新 `title` 与图标，**不每轮重建菜单**（跨线程频繁操作 HMENU 有风险）。

---

## 7. 线程模型

| 线程 | 职责 | 禁忌 |
|------|------|------|
| 托盘线程（pystray） | 托盘图标事件循环、菜单回调 | 禁止直接 `subprocess.Popen` / `os.startfile` / 建 `Toplevel` |
| Tk 主线程（mainloop） | 全部 GUI、进程启动、动画 | — |
| 后台探测线程 | 定时 HTTP 健康检查 | — |

回调统一封装为 `lambda: self.post_ui(...)`（见 `make_handler()`）。`post_ui()` 将 callable 放入 `_ui_queue`，由 `_pump_ui()` 在 Tk 主线程每 50ms 取出执行，失败经 `try/except` 转 `PetBubble` 气泡 + 写日志。**新增动作类型必须复用此封装。**

---

## 8. 桌面悬浮窗

### 8.1 窗口属性

```python
root.overrideredirect(True)                       # 无标题栏
root.attributes('-topmost', True)                 # 始终置顶
root.attributes('-transparentcolor', '#010101')    # 透明背景色
root.geometry(f'{w}x{h}+{x}+{y}')                 # 定位（退出时保存 pet_x/pet_y）
```

### 8.2 交互

- **拖拽**：桌面宠物上按住拖拽移动，松手后坐标写回 `config.json`。
- **双击**：当前未绑定任何行为（代码中 `_on_pet_double_click` 为空实现），不触发动作。后续如需绑定可在该函数内 `post_ui` 调用目标动作。
- **右键**：弹出与托盘同源的菜单（`_show_pet_menu`）。

### 8.3 动画

`start_animation()` / `_animate()` 以约 800ms 间隔在两帧间切换（呼吸效果），状态变化时切换对应状态的帧序列。

### 8.4 隐藏 / 显示

- **隐藏到托盘**：右键"隐藏到托盘" → `root.withdraw()`，托盘图标保留。
- **显示 AI 助理**：托盘菜单"显示/隐藏 AI 助理" → `root.deiconify()`。
- `pet_visible=false` 时启动不创建悬浮窗，仅托盘。

### 8.5 对话气泡 `PetBubble`

`PetBubble` 是依附于桌宠的轻量级对话式提示框，替代传统右下角弹出 Toast：

- **单实例**：新的通知会更新同一气泡内容并重置自动隐藏计时器，避免堆叠弹窗。
- **位置**：桌宠可见时出现在宠物上方（水平居中），若超出屏幕上边缘则翻转到宠物下方；桌宠隐藏时退化为屏幕右下角气泡。
- **外观**：圆角矩形 + 小三角尾巴，根据 `level` 显示不同主题色：
  - `info`：浅蓝背景 / 蓝色标题
  - `ok`：浅绿背景 / 绿色标题
  - `warn`：浅黄背景 / 黄色标题
  - `error`：浅红背景 / 红色标题
- **行为**：自动隐藏（默认 4s），点击气泡立即关闭。
- **入口**：所有 `self.notify(title, message, level)` 调用最终都路由到 `self.bubble.show(...)`，菜单动作执行结果、异常、健康状态变化均通过气泡反馈。

---

## 9. 系统托盘

### 9.1 图标

中性机器人图标（`generate_tray_icons()` 程序化生成并落盘 `icons/*.ico`）。悬浮宠物显示时托盘图标常驻；隐藏时托盘是菜单入口。

### 9.2 托盘右键菜单（完整结构）

```
┌─────────────────────────────┐
│ 🟢 桌面AI助理                │  (禁用标题)
├─────────────────────────────┤
│ 📦 Nanobot 🟢               │
│   ├─ 🟢 Nanobot              │  (禁用子标题，不显示 HTTP 200 等详情)
│   ├─ 🌐 启动 Gateway         │
│   ├─ 🔄 重启 Gateway         │
│   ├─ 🖥️ 打开 WebUI           │
│   ├─ 📂 跳转工作空间         │
│   ├─ 📱 微信渠道授权         │
│   ├─ ⚙️ 查看运行配置         │
│   └─ 📄 查看运行日志         │
├─────────────────────────────┤
│ 👁 显示/隐藏 AI 助理         │  (default 项)
│ 🔄 立即检测                  │
│ ⚙️ 开机自启     (✓/✗ 勾选)   │
│ 📝 编辑助理配置              │
│ ❌ 退出                      │
└─────────────────────────────┘
```

桌宠右键菜单结构同源，差异在于"显示/隐藏"文案随当前状态切换（隐藏时显示"显示AI助理"，显示时显示"隐藏到托盘"）。

---

## 10. 动作执行引擎

### 10.1 分发链

```
make_handler(action, monitor)
   → post_ui(lambda: _execute_action(action, monitor))
      → atype = _action_type(action)
      → fn = getattr(self, "_do_" + atype)
      → fn(action, monitor)        # 全量 try/except
```

- `_action_type()`：优先取 `type` 字段；兼容旧配置（`url`/`file`/`cmd` 作为键名）；否则 `noop`。
- `_execute_action()`：记录日志、按类型分发、异常转 `PetBubble` 气泡 + 文件日志。
- `_do_<type>()`：具体实现。

### 10.2 脚本日志（tee 管道）

`script` / `script_seq` 的 `tee_log=true` 步骤经 `build_tee_powershell()` 生成 PowerShell，用 `System.IO.StreamWriter`（UTF-8 无 BOM + AutoFlush）将脚本输出实时写入 `log_path`，供"查看运行日志"窗口尾随。**禁用 PowerShell `Tee-Object`**（PS 5.1 仅写 UTF-16）。

`script_seq` 在独立后台线程执行多步，步间 `delay_s` 间隔，避免阻塞 UI 线程。

### 10.3 弹窗

- **ConfigViewer**（`popup_file`）：只读文本框展示文件内容，JSON 自动美化；单例窗口（同文件只开一个）；支持复制路径、外部打开。
- **LogViewer**（`popup_log`）：实时尾随日志文件（`refresh_ms` 定时重读、`tail_lines` 限制初始行数），关键字过滤、自动滚动、按级别颜色高亮（ERROR 红 / WARN 黄）；单例窗口。

所有弹窗经 `_singleton_window()` 去重，避免重复堆叠。

---

## 11. 健康检查

- 后台线程 `_probe_worker` 对每启用 `monitor` 执行 `check_monitor()`（HTTP GET `endpoint`）。
- `httpx` 优先；缺失时回退 `urllib`。
- 异常不直接抛出，转为状态 `error` + 友好文案（如"连接被拒绝"/"连接超时"）。
- 结果经 `_apply_results()` 聚合为 `current_state`，驱动托盘图标、桌宠帧与菜单状态 emoji。
- 默认间隔 30s（`check_interval_s`）；支持"立即检测"手动触发。

---

## 12. 异常可见化与日志

- 日志文件：`logs/aipet.log`（UTF-8，RotatingFileHandler，2MB 轮转×3）。
- `sys.excepthook` 与 `threading.excepthook` 重定向到 `aipet.log`，未捕获异常不消失。
- 每个动作包 `try/except`：失败 → `log.exception(...)` + 应用内 `PetBubble` 气泡（非系统通知）。
- 启动方式 `pythonw`，无 stdout/stderr，**禁止依赖控制台输出调试**。

日志样例：

```
2026-08-06 17:12:13 [INFO ] 执行动作: 启动 Gateway (type=script)
2026-08-06 17:12:15 [INFO ] [通知] ▶ 启动 Gateway | start-gateway.bat
2026-08-06 17:12:30 [INFO ] 执行动作: 查看运行日志 (type=popup_log)
2026-08-06 17:13:02 [ERROR] 动作执行失败: 微信渠道授权
Traceback (most recent call last):
  ...
```

---

## 13. 编码与 Windows 坑位

- 所有落盘文本（日志、配置导出）必须 **UTF-8 无 BOM**。
- **禁用 `Tee-Object`** 写日志（PS 5.1 UTF-16 编码问题）；改用 `StreamWriter`。
- 启动脚本（`*.bat`）首行 `chcp 65001 >nul`，文件须 **CRLF** 换行（LF 被 `cmd.exe` 误解析）。
- 路径用 `expand()` + 配置变量，不写死绝对用户路径。

---

## 14. 启动与生命周期

### 14.1 正常启动

```
pythonw aipet.py
  → load_config()
  → setup(): 生成/缓存 pets/robot/*.png 与 icons/*.ico
  → 若 pet_visible=true: 创建悬浮宠物窗口（默认右下角或上次坐标）
  → 创建系统托盘图标（含菜单）
  → 启动健康检查（后台线程）
  → 进入 tkinter mainloop（同时 pystray 在托盘线程运行）
```

### 14.2 开机静默启动

`start.vbs` → 拷贝到 Startup 目录（`desktop-aipet.vbs`）：

```vbs
CreateObject("WScript.Shell").Run "pythonw E:\Portfolio\desktop-aipet\aipet.py", 0, False
```

托盘菜单"⚙️ 开机自启"可切换该快捷方式（`_toggle_startup`）。

### 14.3 退出

- 保存宠物坐标到 `config.json`（`_save_config`）。
- 销毁 tkinter root → 停止 pystray。
- **不影响**被监控服务。

---

## 15. 与 Guardian 的关系

| 项目 | Desktop AIPet | Guardian |
|------|---------------|----------|
| 定位 | 桌面助理 + 状态可见 | 后台保活守护进程 |
| 用户可见性 | ✅ 托盘 + 桌宠 | ❌ 完全隐形 |
| 交互方式 | 拖拽 + 右键菜单 + 桌宠对话气泡 | 无交互 |
| 监控范围 | 多工具（可配置） | 仅 Nanobot Gateway |
| 重启能力 | 右键手动（重启 Gateway） | 自动检测重启 |

两者并存不冲突。AIPet 通过 `auto_restart_on_failure` 可在后期接管 Guardian 的自动重启职责。

---

## 16. 扩展性

- **新增监控目标**：在 `config.json` 的 `monitors[]` 追加一项（含 `endpoint` 与 `actions[]`）。
- **新增菜单动作**：在目标 monitor 的 `actions[]` 追加一个带 `type` 的对象；若类型未实现，新增 `_do_<type>` 方法即可，分发链自动识别。
- **多主题桌宠**：当前内置 robot；如需新形象，扩展 `generate_pet_frames()` 的主题分支。
- **宠物动画**：当前两帧呼吸切换，可扩展为多帧序列。

---

## 17. 文档关系

| 文件 | 性质 | 适用对象 |
|------|------|----------|
| `aipet-design.md`（本文件） | 架构 / 设计说明 | 人类读者、概览 |
| `aipet-spec.md` | SDD 硬约束 | AI 编码代理（修改 `aipet.py` / `config.json` 前必读） |

任何对 `aipet.py` / `config.json` 的改动不得违反 `aipet-spec.md` 中定义的线程模型、状态系统、动作类型系统、编码规则与反模式清单。
