# 桌面 AI 助理 (Desktop AIPet)

一个常驻 Windows 桌面的轻量级 AI 助理：以 **系统托盘图标 + 可拖拽悬浮桌宠** 双形态陪伴你，监控本地 AI 工具（Nanobot Gateway、Claude Code、Codex、OpenCode 等）的运行状态，并提供一个完全由配置驱动的右键菜单，用来启停服务、跳转官网/WebUI/工作空间、查看配置与日志。

> 所有菜单、监控项、AI 助理、提醒都可以只改 `config.json` 来增删改，**无需改代码**。

---

## ✨ 功能特性

- **双形态常驻**：托盘图标 + 悬浮桌宠，二者共享同一套状态与菜单。
- **多工具状态可见**：对已配置 `endpoint` 的服务做 HTTP 健康探测，菜单标题与子项用 🟢/🟡/🔴 实时反映健康。
- **配置化菜单**：菜单动作、监控目标、路径、AI 助理全部走 `config.json`，零代码扩展。
- **AI 助理快捷入口**：Claude Code / Codex / OpenCode 一键启动、打开官网、跳转工作空间、查看运行配置与 Agent 配置。
- **久坐提醒**：按时段定时气泡提醒你起身活动，可一键开启/关闭/测试。
- **桌宠陪伴**：可拖拽、单击眨眼、双击庆祝并随机说一句台词（带对 ded 你的专属称呼）。
- **开机自启**：托盘菜单一键开关，自动写入 Startup 目录。
- **热重载**：改完 `config.json` 后点「🌀 重载配置」即时生效，无需重启。
- **离线可靠**：以应用内对话气泡替代系统通知，适配内网/离线环境。

---

## 🖥️ 运行环境

| 项 | 要求 |
|----|------|
| 操作系统 | Windows 10 / 11 |
| Python | 3.10+（需 `pythonw` 启动，无控制台） |
| 依赖 | `pystray`、`Pillow`、`httpx`（可选，缺则回退 `urllib`）；`tkinter` 为标准库 |
| 启动方式 | `pythonw aipet.py` |

### 安装依赖

```bash
pip install pystray Pillow httpx
```

---

## 🚀 快速开始

1. 双击或运行 `start.vbs`，或在项目目录执行：

   ```bash
   pythonw aipet.py
   ```

2. 桌面上会出现一个机器人桌宠，系统托盘（通知区域）也会出现它的图标。
3. **右键托盘图标**或**右键桌宠**打开完整菜单。

### 开机自启

托盘菜单 → 「📂 更多」→「⚙️ 开机自启」即可；再次点击关闭。开关状态会在菜单标签上以 `[✓]` / `[ ]` 显示。

---

## 🕹️ 使用说明

### 托盘右键菜单结构

```
🟢 桌面AI助理[-昵称]          ← 顶层状态项（聚合 🟢/🟡/🔴）
─────────────────────────
❤️ 久坐提醒
   ├─ 🔛 开启提醒   [ ]
   ├─ ⏸ 关闭提醒   [✓]        ← 当前生效项带 [✓]
   └─ 🔔 测试提醒
🟢 Nanobot
   ├─ 🌐 启动 Gateway
   ├─ 🔄 重启 Gateway
   ├─ 🏠 打开官网
   ├─ 🖥️ 打开 WebUI
   ├─ 📂 跳转工作空间
   ├─ 💬 微信渠道授权
   ├─ ⚙️ 查看运行配置
   └─ 📄 查看运行日志
🌟 Claude Code / 💠 Codex / 🔮 OpenCode
   ├─ 🚀 启动 <助理>
   ├─ 🌐 打开官网
   ├─ 📁 跳转工作空间
   ├─ ⚙️ 查看运行配置
   └─ 📄 查看 Agent 配置
─────────────────────────
🙈 隐藏助理 / ✨ 召唤助理     ← 随桌宠可见性动态切换
📂 更多
   ├─ 🔄 健康检测
   ├─ ⚙️ 开机自启   [✓]
   ├─ 🛠️ 助理配置
   └─ 🌀 重载配置
─────────────────────────
🔁 重启
❌ 退出
```

### 桌宠交互

| 操作 | 效果 |
|------|------|
| 拖拽 | 移动桌宠；松手后位置自动保存 |
| 左键 / 右键单击 | 机器人**眨眼**（不弹健康提示） |
| 双击 | **庆祝动画**（弹跳 + 摆动 + 弯眼）+ 随机展示一句台词 |
| 右键 | 弹出与托盘同源的菜单 |

> 注意：单击只眨眼、不弹健康信息；想看健康状态请打开菜单或使用「📂 更多 → 🔄 健康检测」。

### 召唤 / 隐藏

- 桌宠可见时菜单显示「🙈 隐藏助理」，点击后仅留托盘。
- 桌宠隐藏时菜单显示「✨ 召唤助理」，点击后重新显示桌宠并随机说一句台词。

### 久坐提醒

- 在「❤️ 久坐提醒」子菜单里：**开启提醒 / 关闭提醒 / 测试提醒**。
- 当前生效的那一项会带 `[✓]`。
- 开启后会在 `start_hour`–`end_hour` 时段内，按 `interval_minutes` 间隔（跳过 `skip_hours` 整点）弹气泡提醒。
- 「测试提醒」无视时段立即弹一次，用于验证效果。

---

## ⚙️ 自定义配置

所有配置集中在项目根目录的 **`config.json`**。修改后点击托盘菜单「📂 更多 → 🌀 重载配置」即可生效；也可经由「🛠️ 助理配置」打开只读查看器（可切编辑态修改并保存，保存即热重载）。

### 顶层结构

```json
{
  "desktop_aipet": { },   // 桌宠与全局开关、昵称/称呼
  "menus": [ ],           // 菜单组（健康监控 / AI 助理 / 久坐提醒），顺序即显示顺序
  "greetings": [ ]        // 桌宠双击/召唤时随机展示的台词（纯文案）
}
```

> 早期版本用 `monitors` 键，**现已统一为 `menus`**，旧键不再被读取。

### `desktop_aipet` 字段

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `pet_theme` | `"robot"` | 桌宠形象主题，内置：`robot`、`labrador`（拉布拉多）、`bluecat`（蓝猫）、`piggy`（小猪）、`bunny`（小白兔）、`wukong`（孙悟空）、`pony`（小马）。右键托盘 →「🔄 切换助理」循环切换，并同步切换托盘图标 |
| `pet_visible` | `true` | 是否显示悬浮桌宠；`false` 时仅托盘 |
| `pet_scale` | `1.0` | 缩放比例（高分屏适配） |
| `pet_x` / `pet_y` | 运行时保存 | 桌宠退出时的坐标 |
| `check_interval_s` | `30` | 健康检测间隔（秒） |
| `log_retention_days` | `7` | 日志保留天数（参考值） |
| `auto_restart_on_failure` | `false` | 失败自启开关（未强制启用） |
| `nickname` | `""` | 助理昵称；非空时显示「桌面AI助理-昵称」 |
| `boss` | `"老板"` | 对用户的称呼；台词前自动拼接「{boss}，」 |

**示例**：把助理叫「小新」，对你称呼「老板」：

```json
"desktop_aipet": {
  "nickname": "小新",
  "boss": "老板"
}
```

### `menus[]` —— 三类菜单组

每个菜单组是一个对象，按数组顺序显示。根据字段分为三类：

#### 1) 健康监控组（带 `endpoint`，参与探活）

```json
{
  "name": "Nanobot",
  "endpoint": "http://127.0.0.1:18790/health",
  "enabled": true,
  "config_path": "%USERPROFILE%\\.nanobot\\config.json",
  "log_path": "%USERPROFILE%\\.nanobot\\logs\\gateway.log",
  "workspace_key": "agents.defaults.workspace",
  "actions": [ /* 见下方动作 */ ]
}
```

菜单前缀为状态圆圈 🟢/🔴（不读 `icon`）。

#### 2) AI 助理组（`type: "ai_assistant"`，不探活）

```json
{
  "name": "Claude Code",
  "type": "ai_assistant",
  "icon": "🌟",
  "enabled": true,
  "actions": [
    {"label": "启动 Claude Code", "icon": "🚀", "type": "powershell",
     "command": "claude --permission-mode bypassPermissions",
     "window": "visible", "keep_open": true},
    {"label": "打开官网", "icon": "🌐", "type": "url",
     "url": "https://claude.com/claude-code"},
    {"label": "跳转工作空间", "icon": "📁", "type": "open_workspace",
     "path": "%USERPROFILE%\\.claude"},
    {"label": "查看运行配置", "icon": "⚙️", "type": "popup_file",
     "path": "%USERPROFILE%\\.claude\\settings.json", "title": "Claude Code 运行配置"},
    {"label": "查看 Agent 配置", "icon": "📄", "type": "popup_file",
     "path": "%USERPROFILE%\\.claude\\CLAUDE.md", "title": "Claude Code Agent 配置"}
  ]
}
```

菜单前缀为 `icon`（如 🌟）。`enabled: false` 时该菜单项**完全不出现在右键菜单中**，也不做健康检测（适合非技术用户把不需要的技术类菜单整体关闭）。

#### 3) 久坐提醒组（`type: "health_reminder"`，不探活）

```json
{
  "name": "久坐提醒",
  "type": "health_reminder",
  "icon": "❤️",
  "enabled": true,
  "reminder_enabled": true,
  "start_hour": 7,
  "end_hour": 22,
  "interval_minutes": 60,
  "skip_hours": [9],
  "message": "该起身活动一下身体啦～伸个懒腰、走两步，别久坐伤身哦！",
  "actions": [
    {"type": "reminder_enable", "icon": "🔛", "label": "开启提醒"},
    {"type": "reminder_disable", "icon": "⏸", "label": "关闭提醒"},
    {"type": "reminder_test",  "icon": "🔔", "label": "测试提醒"}
  ]
}
```

- `start_hour`/`end_hour`：生效时段（24 小时制）。
- `interval_minutes`：提醒间隔（分钟）；兼容旧字段 `interval_hour`（小时）。
- `skip_hours`：跳过的整点小时数组（如午休的 9 点）。
- `message`：提醒气泡文案。
- `enabled`：是否展示该菜单项（`false` 则整组不出现在右键菜单）。
- `reminder_enabled`：提醒是否真正触发（默认 `true`）；与 `enabled` 独立，由组内「开启/关闭提醒」切换。
- 三个子动作（`reminder_*`）**仅在本组内有效**，当前生效项标签带 `[✓]`。

### `greetings` 随机台词

```json
"greetings": [
  "今天心情怎么样？",
  "需要我帮你做什么？",
  "摸鱼时间到啦～"
]
```

- 纯文案，**不要**在句子里写称呼；代码会自动在前面拼接「{boss}，」。
- 出现在：双击桌宠、点击「✨ 召唤助理」时。

### 动作类型总表

每个动作是一个对象，必须声明 `type`，可带 `label`（菜单文字）与 `icon`（前置图标）。

| `type` | 用途 | 关键字段 |
|--------|------|----------|
| `url` | 打开浏览器/地址 | `url` |
| `file` | 用默认程序打开文件 | `path` / `file` |
| `cmd` | 执行 cmd 命令 | `command`/`cmd`、`window`(`visible`/`hidden`)、`keep_open` |
| `powershell` | 执行 PowerShell 命令 | `command`、`window`、`keep_open` |
| `script` | 执行 `.bat`/`.ps1` 脚本 | `path`、`window`、`tee_log`、`log_path`、`append_log`、`keep_open` |
| `script_seq` | 顺序执行多步（如 停止→启动） | `steps[]`、`delay_s`（步间延迟秒） |
| `open_workspace` | 打开文件夹（直接给 `path`，或读配置 workspace 字段） | `path` 或 `config_path`+`config_key` |
| `popup_file` | 只读文件查看器（JSON 自动格式化） | `path`、`title`、`pretty_json` |
| `popup_log` | 实时尾随日志窗口 | `path`/`log_path`、`refresh_ms`、`tail_lines`、`title`、`hint` |
| `reminder_enable` / `reminder_disable` / `reminder_test` | 久坐提醒开关/测试（仅 `health_reminder` 组内） | `label`、`icon` |

> 想新增一种动作？实现 `_do_<type>` 方法即可，分发链自动识别，无需改动其它代码。

### 路径约定

- 所有路径支持 `%USERPROFILE%`、`~` 自动展开，**不要**写死绝对用户路径。
- 示例：`%USERPROFILE%\.nanobot`、`%USERPROFILE%\.claude`、`%USERPROFILE%\.codex`、`%USERPROFILE%\.config\opencode`。

---

## 🔄 热重载与查看配置

- **🌀 重载配置**：重新读取 `config.json`，刷新菜单、昵称、称呼、台词、久坐提醒，无需重启。
- **🛠️ 助理配置**：只读查看 `config.json`；可切到编辑态修改，保存时做 JSON 合法性校验，成功即触发热重载。
- **退出不写回**：点击「❌ 退出」**不会**覆盖 `config.json`，避免冲掉你已改好的配置。仅「隐藏/显示桌宠」与「开启/关闭久坐提醒」会即时持久化相应开关。

---

## 🩺 健康检测与状态

- 仅**带 `endpoint` 的菜单组**参与探活（如 Nanobot）。
- 后台每 `check_interval_s` 秒做一次 HTTP GET；也可手动「🔄 健康检测」。
- 状态聚合：全正常 → 🟢；有正常有异常 → 🟡；全异常 → 🔴。
- AI 助理与久坐提醒组**不参与**健康聚合，只用各自 `icon` 作前缀。

---

## 📋 常见问题

**Q：桌宠挡在屏幕上怎么办？**
A：直接拖到角落；或右键托盘「🙈 隐藏助理」只留托盘；或把 `desktop_aipet.pet_visible` 设为 `false` 后再重载配置。

**Q：改了 config.json 没反应？**
A：托盘菜单「📂 更多 → 🌀 重载配置」即可，无需重启。若仍无变化，检查 JSON 是否合法（「🛠️ 助理配置」保存时会校验）。

**Q：怎么知道服务是否健康？**
A：看托盘/菜单标题的 🟢/🟡/🔴；或点「🔄 健康检测」手动探一次。

**Q：日志在哪里？**
A：项目目录下 `logs/aipet.log`（UTF-8）。动作失败、异常都会落这里。

**Q：可以加自己的 AI 工具吗？**
A：可以。在 `menus[]` 追加一个 `type:"ai_assistant"` 组，配置图标、启动命令、官网、工作空间与配置文件路径即可。

---

## 📁 项目结构

```
desktop-aipet/
├── aipet.py            # 主程序（全部逻辑）
├── config.json         # 监控与菜单配置（核心可配置项）
├── start.vbs           # 开机静默启动脚本源
├── README.md           # 本文件
├── aipet-design.md     # 架构 / 设计说明
├── aipet-spec.md       # 开发硬约束（SDD）
├── pets/robot/         # 桌宠形象 PNG（首次启动生成）
├── icons/              # 托盘 ICO（首次启动生成）
└── logs/aipet.log      # 运行日志
```

---

## 📄 相关文档

- **设计说明**：`aipet-design.md`
- **开发规范（SDD 硬约束）**：`aipet-spec.md`
