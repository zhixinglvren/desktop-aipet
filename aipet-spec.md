# desktop-aipet 规范文档 (Spec)

> 作者: zhixinglvren
> 适用版本: aipet.py (配置驱动型, 全部托盘/桌宠动作由 config.json 定义)
> 方法论: SDD (Spec-Driven Development) — 本文件是 AI 编码代理必须遵循的硬约束,
>         不是自然语言描述。任何对 `aipet.py` / `config.json` 的改动都不得违反下列规则。

---

## 1. 项目目标

轻量级 Windows 桌面 AI 助理:

- 系统托盘图标 + 悬浮桌宠, 二者共享同一套健康状态与右键菜单。
- 通过 HTTP 健康探测监控若干后端服务 (当前为 Nanobot Gateway)。
- 对 AI 编程助理 (Claude Code / Codex / OpenCode)、久坐提醒等以**纯菜单组**形式提供快捷动作, 不探活。
- 托盘/桌宠菜单提供启停、官网/WebUI 跳转、工作空间跳转、渠道授权、配置与日志查看。
- **所有菜单行为必须配置化**, 不允许在代码里硬编码动作逻辑。

---

## 2. 运行环境约束 (关键)

| 项 | 值 |
|---|---|
| 操作系统 | Windows 10/11 |
| 启动方式 | `pythonw aipet.py` (无控制台) |
| 开机自启 | 通过 `start.vbs` 拷贝到 Startup 目录实现 |
| 核心依赖 | `pystray` (托盘), `tkinter` (弹窗, 标准库), `Pillow` (图标绘制), `httpx` (健康探测, 可选, 缺失时回退 `urllib`) |
| 日志落盘 | `logs/aipet.log` (UTF-8, RotatingFileHandler) |

**硬规则**: 程序以 `pythonw.exe` 启动, **没有 stdout/stderr**。任何依赖控制台输出调试的做法都会静默失败。

---

## 3. 线程模型 (最高优先级, 违反即静默崩溃)

这是本项目最容易出错、也最容易被 AI 编码代理忽略的点。

- `pystray.Icon.run()` 运行在**独立线程** (托盘线程)。托盘菜单回调、双击等事件均在该线程触发。
- `tkinter` 的全部 GUI 操作 (创建 `Toplevel`、弹 `Menu`、操作控件) 必须在 **Tk 主线程** (即 `root.mainloop()` 所在线程) 执行。
- 跨线程直接操作 tkinter 会被 `pythonw` 吞掉异常, 表现为**菜单点击无反应、无任何报错**。

**规则 (必须遵守)**:

1. 任何托盘/桌宠菜单回调只能调用 `self.post_ui(fn)`, 把真实逻辑编组到 Tk 主线程。
2. 回调内**禁止**直接调用 `os.startfile` / `subprocess.Popen` / `tk.Toplevel` / `tk.Menu`。
3. `post_ui` 经由 `self._ui_queue` 队列 + `_pump_ui` ( Tk 主线程每 50ms 轮询) 执行, 失败会被 `try/except` 捕获并转为 `PetBubble` 对话气泡 + 文件日志。

参考实现: `make_handler()` 已经把所有回调统一包成 `lambda: self.post_ui(...)`, 新增动作类型时**复用此封装, 不要自己写裸回调**。

---

## 4. 状态系统

| 状态 | 颜色 | 含义 |
|---|---|---|
| `normal` | `#22c55e` (绿) | 所有健康监控项正常 |
| `warning` | `#f59e0b` (黄) | 部分异常 (有正常有异常) |
| `error` | `#ef4444` (红) | 全部异常 |

**表现位置**:

- **机器人图标 / 桌宠**: 保持中性配色, **不叠加**任何状态圆点/徽标, 天线灯保持中性主题色。
- **顶层菜单标题**: 固定显示 "🟢/🟡/🔴 桌面AI助理（或 -昵称）", 使用**聚合状态**。
- **各健康监控子菜单标题**: 显示 "🟢 MonitorName" 或 "🔴 MonitorName", **仅二态** (正常=绿, 异常=红), 不显示 "HTTP 200" 等探测详情。
- **AI 助理 / 久坐提醒组**: **不显示状态圆圈**, 改用各自 `icon` 作为菜单前缀 (如 🌟/💠/🔮/❤️)。
- **鼠标悬停提示 (tooltip)**: 显示 "桌面AI助理" 或 "桌面AI助理-昵称" (含昵称时), 不带 emoji / 状态摘要 / 圆点。

**文字规则**: 菜单标题只写 "桌面AI助理" 或监控名, **不写** "全部正常"/"异常"/"HTTP 200" 等状态摘要文字。

**派生规则**: 仅由带有 `endpoint` 且启用的菜单组的健康结果聚合 — 全正常→`normal`/`🟢`; 有正常有异常→`warning`/`🟡`; 全异常→`error`/`🔴`。`warning` 仅出现在聚合层, 单个监控项没有黄色状态。AI 助理与久坐提醒**不参与**健康聚合。

**更新规则**: 状态变化时调用 `update_tray_icon(state, changed=True)` 会同时换菜单状态 emoji + 重建菜单; 平时只换 `title`, **不要每轮都 `update_menu()`** (跨线程频繁操作 HMENU 有风险)。参考 `update_tray_icon()`。

---

## 5. 动作类型系统 (配置化核心)

每个菜单动作是一个 JSON 对象, **必须显式声明 `type`**。旧配置用 `url`/`file`/`cmd` 作为键名仍可兼容 (`_action_type` 会回退识别), 但新配置一律用 `type` 字段。每个动作可带 `label` (菜单文字) 与 `icon` (前置图标 emoji)。

| `type` | 用途 | 关键字段 |
|---|---|---|
| `url` | 打开浏览器/地址 | `url` |
| `file` | 用默认程序打开文件 | `path`/`file` |
| `cmd` | 执行 cmd 命令 | `command`/`cmd`, `window`(`visible`/`hidden`), `keep_open` |
| `powershell` | 执行 PowerShell 命令 | `command`, `window`, `keep_open` |
| `script` | 执行 `.bat`/`.ps1` 等脚本 | `path`, `window`, `tee_log`, `log_path`, `append_log`, `keep_open` |
| `script_seq` | 顺序执行多步 (如 停止→启动) | `steps[]`, `delay_s` (步间延迟秒) |
| `open_workspace` | 打开文件夹: 直接给 `path`, 或读取配置中 workspace 字段 | `path` 或 `config_path`+`config_key` (或 monitor 级 `workspace_key`) |
| `popup_file` | 弹出只读文件查看器 (JSON 自动格式化) | `path`, `title`, `pretty_json` |
| `popup_log` | 弹出实时尾随日志窗口 | `path` (或 monitor 级 `log_path`), `refresh_ms`, `tail_lines`, `title`, `hint` |
| `reminder_enable` | 开启久坐提醒 (**仅** `health_reminder` 组内有效) | `label`, `icon` |
| `reminder_disable` | 关闭久坐提醒 (**仅** `health_reminder` 组内有效) | `label`, `icon` |
| `reminder_test` | 立即测试弹一次提醒 (**仅** `health_reminder` 组内有效) | `label`, `icon` |

各 `step` (用于 `script`/`script_seq`) 支持的字段:
`path`, `window`(`visible`/`hidden`), `wait`(是否等待完成), `timeout_s`, `tee_log`(将输出同时写入日志), `append_log`, `keep_open`, `log_path`, `env`(字典, 注入环境变量)。

动作解析入口: `_action_type()` → `_execute_action()` → `_do_<type>()`。新增类型只需实现 `_do_<type>` 并在 `_execute_action` 通过 `getattr` 分发, **无需改动分发逻辑**。

---

## 6. 菜单配置 Schema

`config.json` 顶层结构:

```json
{
  "desktop_aipet": { /* 桌宠与全局开关、nickname/boss, 见第 10 节 */ },
  "menus": [
    {
      "name": "Nanobot",
      "endpoint": "http://127.0.0.1:18790/health",
      "enabled": true,
      "config_path": "%USERPROFILE%\\.nanobot\\config.json",
      "log_path": "%USERPROFILE%\\.nanobot\\logs\\gateway.log",
      "workspace_key": "agents.defaults.workspace",
      "actions": [ /* 见第 11 节 */ ]
    },
    {
      "name": "Claude Code",
      "type": "ai_assistant",
      "icon": "🌟",
      "enabled": true,
      "actions": [ /* type=powershell/url/open_workspace/popup_file ... */ ]
    },
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
      "message": "该起身活动一下身体啦～",
      "actions": [
        {"type": "reminder_enable", "icon": "🔛", "label": "开启提醒"},
        {"type": "reminder_disable", "icon": "⏸", "label": "关闭提醒"},
        {"type": "reminder_test", "icon": "🔔", "label": "测试提醒"}
      ]
    }
  ],
  "greetings": [ /* 桌宠双击/召唤时随机展示的台词, 纯文案, 见第 9 节 */ ]
}
```

**`menus[]` 三类菜单组 (顺序即菜单显示顺序)**:

- **健康监控组**: 含 `endpoint` → 参与探活, 菜单前缀为状态圆圈 (🟢/🔴), 忽略 `icon`。
- **AI 助理组**: `type="ai_assistant"` → 不探活, 菜单前缀为 `icon` (如 🌟)。`enabled=false` 时 **不在右键菜单出现**, 也不探活。
- **久坐提醒组**: `type="health_reminder"` → 不探活, 菜单前缀为 `icon` (如 ❤️)。`enabled=false` 时整组不展示; `enabled=true` 时菜单展示, 其下「开启/关闭提醒」切换独立的 `reminder_enabled` 字段 (与 `enabled` 无关)。

- `actions[]` 的顺序 **即菜单显示顺序**, 不要依赖任何隐式排序。
- `config_path` / `log_path` / `workspace_key` 为健康监控组的动作提供默认值 (动作内未指定时用组级值)。
- 路径统一用 `expand()` 展开 (`%USERPROFILE%`、`~` 均支持), **不要写死绝对路径**。

---

## 7. 异常可见化 (pythonw 下)

- `sys.excepthook` 与 `threading.excepthook` 已重定向到 `logs/aipet.log`, 未捕获异常不会消失。
- 每个动作执行包在 `try/except` 中, 失败 → `log.exception(...)` + `notify(...)` (应用内 `PetBubble` 对话气泡, 不依赖系统通知 API)。
- 健康探测异常不直接抛, 转成状态 `error` 与友好文案 (如 "连接被拒绝"/"连接超时")。
- 久坐提醒异常在 `_health_reminder_tick` 内 `try/except` 捕获并记日志, 不影响主循环。

### 7.1 `PetBubble` 约束

- 所有 `notify(title, message, level)` 最终路由到 `self.bubble.show(...)`。
- `PetBubble` 是**单实例**：新通知更新同一气泡内容并重置自动隐藏计时器，禁止堆叠多个弹出框。
- 桌宠可见时气泡必须依附桌宠（上方或下方，视屏幕边界自动翻转）；桌宠隐藏时退化为屏幕右下角气泡。
- 气泡样式：圆角矩形 + 三角尾巴，背景/标题色随 `level` 变化（info/ok/warn/error 四色主题）。
- 默认 4s 自动隐藏（提醒类 8s），点击气泡立即关闭。

**禁止**假设"跑一下看报错"在 `pythonw` 下有效 — 没有控制台就看不到错误。

---

## 8. 编码规则 (Windows 坑位)

- 所有写入磁盘的文本 (日志、配置导出) 必须 **UTF-8 无 BOM** (`save_config` 用 `ensure_ascii=False, indent=2` 直接 `json.dump` 到临时文件再 `os.replace`)。
- **禁止**使用 PowerShell 的 `Tee-Object` 写日志: PS 5.1 下它只写 UTF-16, 导致日志窗口乱码/无法增量解析。
  改用 `System.IO.StreamWriter` (UTF-8, 无 BOM) + `AutoFlush` 的模式, 见 `build_tee_powershell()`。
- 启动脚本 (`*.bat`) 必须在首行执行 `chcp 65001 >nul`, 且文件须为 **CRLF 换行** (LF 会被 `cmd.exe` 误解析)。
- 托盘/桌宠菜单 label 使用 emoji 无碍, 但 `.bat`/`.ps1` 内如需中文输出, 确保编码为 UTF-8。
- 桌宠隐藏任务栏条目 (`WS_EX_TOOLWINDOW`) 须在 `-transparentcolor` 之后调用, 否则会被覆盖。

---

## 9. 随机台词与称呼 (greetings / boss / nickname)

- `greetings`: 顶层字符串数组, 存放桌宠双击 / 召唤时随机展示的**纯文案**, 不含量词/称呼。
- `desktop_aipet.boss`: 助理对用户的称呼, 默认 "老板"; 展示时由代码统一在台词前拼接 "「{boss}，」" 前缀 (`_pick_greeting()`), **不要**把称呼写进 `greetings` 元素里。
- `desktop_aipet.nickname`: 助理昵称; 非空时 `assistant_display_name()` 返回 "桌面AI助理-{nickname}", 用于:
  - 托盘悬停提示 (title)
  - 顶层菜单状态项文字
  - 气泡标题
- 三者均在 `load_config` / `_reload_config` 时读取, 热重载即时生效。

---

## 10. 环境路径常量

| 名称 | 值 |
|---|---|
| `NANOBOT_HOME` | `%USERPROFILE%\.nanobot` |
| 启动脚本 | `%USERPROFILE%\.nanobot\start-gateway.bat` |
| 停止脚本 | `%USERPROFILE%\.nanobot\stop-gateway.bat` |
| Gateway 健康端点 | `http://127.0.0.1:18790/health` |
| WebUI | `http://127.0.0.1:8765` |
| Nanobot 官网 | `https://nanobot.wiki` |
| Nanobot 配置 | `%USERPROFILE%\.nanobot\config.json` |
| 工作空间字段 | `agents.defaults.workspace` (值为 `D:\AIGC\Nanobot\WorkSpace`) |
| Gateway 实时日志 | `%USERPROFILE%\.nanobot\logs\gateway.log` (由 tee 脚本首次启动时创建) |
| Claude Code 工作空间 | `%USERPROFILE%\.claude` (运行配置 `settings.json`、Agent 配置 `CLAUDE.md`) |
| Codex 工作空间 | `%USERPROFILE%\.codex` (运行配置 `config.toml`、Agent 配置 `AGENTS.md`、官网 `https://developers.openai.com/codex`) |
| OpenCode 工作空间 | `%USERPROFILE%\.config\opencode` (运行配置 `opencode.json`、Agent 配置 `AGENTS.md`、官网 `https://opencode.ai`) |

`desktop_aipet` 字段说明:
`pet_theme`(主题, 内置 `robot`/`labrador`/`bluecat`/`piggy`/`bunny`/`wukong`/`pony` 七种形象, 托盘「🔄 切换助理」循环切换), `pet_visible`(是否显示桌宠), `pet_scale`(缩放, 默认 1.0),
`pet_x`/`pet_y`(桌宠坐标, 运行时自动保存), `check_interval_s`(健康检测间隔, 默认 30),
`log_retention_days`(日志保留天数), `auto_restart_on_failure`(失败自启开关),
`nickname`(助理昵称, 默认空), `boss`(对用户的称呼, 默认 "老板")。

---

## 11. 菜单配置规范样例 (即 config.json 实际内容)

完整 `config.json` 见仓库内 `config.json`。要点:

- **Nanobot 组** (`endpoint`): 启动 Gateway(`script`, tee 日志) → 重启 Gateway(`script_seq`: 先 stop 后 start, delay 3s) → 打开官网(`url`→nanobot.wiki) → 打开 WebUI(`url`) → 跳转工作空间(`open_workspace`) → 微信渠道授权(`powershell`, 可见) → 查看运行配置(`popup_file`) → 查看运行日志(`popup_log`)。
- **AI 助理组** (`type=ai_assistant`, 各自 `icon`): 启动(`powershell` 调对应 CLI) / 打开官网(`url`) / 跳转工作空间(`open_workspace`, 直接给 `path` 文件夹) / 查看运行配置 / 查看 Agent 配置(`popup_file`)。
- **久坐提醒组** (`type=health_reminder`): 时段 `start_hour`~`end_hour`、间隔 `interval_minutes`、跳过整点 `skip_hours`、文案 `message`; `enabled` 控制菜单是否展示, `reminder_enabled` 控制提醒是否触发; 子项 `reminder_enable`/`reminder_disable`/`reminder_test` 切换 `reminder_enabled`, 当前生效项标签带 `[✓]`。
- **「📂 更多」二级菜单**: 🔄 健康检测 / ⚙️ 开机自启 (`[✓]`/`[ ]`) / 🛠️ 助理配置 (只读 ConfigViewer, 可编辑保存并热重载) / 🌀 重载配置。
- **末尾**: 🔁 重启 / ❌ 退出 (**退出不写回 config.json**)。

---

## 12. 给 AI 编码代理的约束清单 (落地检查)

修改前逐条对照, 任一不满足则视为不合格:

- [ ] 新增菜单行为是否走 `config.json` 的 `type`, 而非代码硬编码?
- [ ] 所有 GUI/进程操作是否经 `post_ui()` 编组到 Tk 主线程?
- [ ] 日志是否 UTF-8 无 BOM? 是否避开了 `Tee-Object`?
- [ ] 异常是否落 `logs/aipet.log` 并通过 `PetBubble` 气泡反馈, 而非静默吞掉?
- [ ] 状态 emoji 是否仅出现在菜单标题与**健康监控**子菜单项? 机器人图标/桌宠无状态徽标? AI 助理/久坐提醒组是否用各自 `icon` 而非状态圆圈?
- [ ] 路径是否用 `expand()` + 配置变量, 无硬编码用户绝对路径?
- [ ] `reminder_*` 类型是否仅出现在 `health_reminder` 组内?
- [ ] `enabled=false` 的菜单组是否**完全不展示**于右键菜单, 且不参与健康检测 (而非灰显 "未启用")?
- [ ] `health_reminder` 组的「开启/关闭提醒」是否只切换 `reminder_enabled`, 与控菜单展示的 `enabled` 相互独立?
- [ ] 退出路径 (`_on_exit`) 是否**不再**调用 `_save_config()`, 以免覆盖用户已改好的配置?
- [ ] 新增 `desktop_aipet` 的 `nickname`/`boss` 或 `greetings` 文案时, 称呼前缀是否由代码统一拼接, 未写死进 `greetings`?
