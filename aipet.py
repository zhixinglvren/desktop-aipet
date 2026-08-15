#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Desktop AIPet — 桌面AI助理

轻量级桌面助理：HTTP 健康探测 + 悬浮桌宠 + 系统托盘菜单。
所有托盘/桌宠菜单动作均由 config.json 驱动，支持自定义。
"""

import json
import os
import sys
import time
import queue
import shutil
import ctypes
import logging
import threading
import math
import random
import subprocess
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime, timedelta
import re
import zipfile
import tempfile
import winreg

# ---------------------------------------------------------------------------
# 依赖自检：第三方模块缺失时给出可读提示。
# 因为 pythonw 没有控制台窗口，若直接 import 失败会被静默吞掉，表现为“什么都没发生”。
# 故在入口处先 try import，失败则用 MessageBox 提示正确运行方式后退出。
# ---------------------------------------------------------------------------
try:
    import tkinter as tk
    from PIL import Image, ImageDraw, ImageTk
    import pystray
    # 托盘图标双击召唤：当前版本 pystray 不支持 double_click 参数，且左键单击会弹出模态菜单
    # 吞掉双击消息。故子类化 win32 实现层，做「左键抬起延时弹菜单 + 双击取消」逻辑。
    from pystray import _win32 as _pystray_win32
    from pystray._util import win32 as _pystray_win32_util
    import httpx
except ImportError as _imp_err:
    _mod = getattr(_imp_err, "name", str(_imp_err))
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            "桌面AI助理无法启动：缺少依赖模块「%s」。\n\n"
            "请使用已安装 pystray / PIL / httpx 的 Python 运行本程序，例如：\n"
            "  D:\\Software\\Python3\\pythonw.exe aipet.py" % _mod,
            "桌面AI助理 启动失败",
            0x10,  # MB_ICONERROR
        )
    except Exception:
        pass
    raise SystemExit(1)

import time as _time
import threading as _threading

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# 路径约定：
#   - 开发态：BASE_DIR = 脚本所在目录；运行期数据与资源都在此。
#   - 冻结态(PyInstaller 单文件夹模式)：
#       EXE_DIR  = 可执行文件所在目录（安装根目录，用户可写）→ 放运行期数据/配置；
#       RES_DIR  = sys._MEIPASS（即打包内的 _internal）→ 只读资源（version.txt / 默认 config.json）。
#     注意 PyInstaller 6.x 会把 --add-data 的文件放到 _internal，故资源与应用数据分层，
#     不能混用 sys.executable.parent 来读 version.txt。
if getattr(sys, "frozen", False):
    EXE_DIR = Path(sys.executable).parent
    # onefile: 资源在 sys._MEIPASS；onedir: 资源在 exe 同级的 _internal 目录
    _meipass = getattr(sys, "_MEIPASS", None)
    RES_DIR = Path(_meipass if _meipass else EXE_DIR / "_internal")
    BASE_DIR = EXE_DIR
else:
    EXE_DIR = RES_DIR = BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
if getattr(sys, "frozen", False):
    # onefile: 资源在 sys._MEIPASS；onedir: 资源在 exe 同级的 _internal 目录
    _meipass = getattr(sys, "_MEIPASS", None)
    PETS_DIR = (Path(_meipass) if _meipass else EXE_DIR / "_internal") / "pets"
else:
    PETS_DIR = BASE_DIR / "pets"
ICONS_DIR = BASE_DIR / "icons"
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# Win32 process creation flags
CREATE_NEW_CONSOLE = 0x00000010
CREATE_NO_WINDOW = 0x08000000

# 版本与更新：单一版本来源为同目录 version.txt；GitHub 仓库可在 config.json 的
# desktop_aipet.update_repo 覆盖，未配置时使用此占位值（需用户填写）。
DEFAULT_UPDATE_REPO = "owner/repo"

# tkinter 透明色（宠物窗口与气泡共用）
TRANSPARENT_COLOR = "#fe01fe"

# ---------------------------------------------------------------------------
# Logging — 必须在 pythonw.exe（无 stdout/stderr）下也能工作
# ---------------------------------------------------------------------------
log = logging.getLogger("desktop-aipet")
log.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s [%(levelname)-5s] %(message)s",
                         datefmt="%Y-%m-%d %H:%M:%S")
_fh = RotatingFileHandler(LOGS_DIR / "aipet.log", maxBytes=2 * 1024 * 1024,
                          backupCount=3, encoding="utf-8")
_fh.setFormatter(_fmt)
log.addHandler(_fh)
# 仅在真正拥有 stdout 时才挂控制台 handler（pythonw 下 sys.stdout is None）
if getattr(sys, "stdout", None) is not None:
    _ch = logging.StreamHandler(sys.stdout)
    _ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)-5s] %(message)s",
                                       datefmt="%H:%M:%S"))
    log.addHandler(_ch)


def _excepthook(exc_type, exc_value, exc_tb):
    log.error("未捕获异常:\n%s",
              "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))


sys.excepthook = _excepthook
threading.excepthook = lambda a: log.error(
    "线程未捕获异常 (%s):\n%s", getattr(a.thread, "name", "?"),
    "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def expand(p):
    """展开 %VAR% 与 ~ 。"""
    if not p:
        return ""
    return os.path.expanduser(os.path.expandvars(str(p)))


def deep_get(data, dotted, default=None):
    """按 'a.b.c' 路径取值。"""
    cur = data
    for part in str(dotted).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def load_config():
    # 安装后首次运行：若根目录无 config.json，则从打包内拷贝默认配置（自举）。
    # 这样用户后续修改持久化在 EXE_DIR/config.json，升级时用 /XF 排除它即可保留。
    if not CONFIG_PATH.exists():
        src = RES_DIR / "config.json"
        if src.exists() and src != CONFIG_PATH:
            try:
                shutil.copy2(src, CONFIG_PATH)
                log.info("已从内置默认配置自举写入 %s", CONFIG_PATH)
            except Exception:
                log.exception("配置自举失败")
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
    else:
        cfg = {"desktop_aipet": {}, "menus": []}
    # pet_character 已废弃，统一以 pet_theme 控制桌宠形象；
    # 加载时即剔除该遗留键，避免被深合并重新写回磁盘。
    cfg.setdefault("desktop_aipet", {}).pop("pet_character", None)
    return cfg


def save_config(cfg):
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def _deep_merge(base, override):
    """深合并 base 与 override，返回新字典/列表。

    规则：
      - 两边都是 dict：递归合并；override 的键覆盖 base，base 独有的键保留。
      - 两边都是 list 且元素均为带「name」的 dict：按 name 逐元素合并，
        这样保存配置时磁盘上用户新增的嵌套键（如 desktop_aipet.nickname、monitors[].actions 里
        的额外字段）不会被
        内存里旧实例的缺字段配置覆盖。
      - 其余类型：override 优先。

    用于 _save_config：以磁盘配置为 base、内存配置为 override，既保留用户外部
    新增内容，又让运行时变更（窗口位置、ConfigViewer 编辑）生效。
    """
    if isinstance(base, dict) and isinstance(override, dict):
        result = dict(base)
        for k, v in override.items():
            if k in result:
                result[k] = _deep_merge(result[k], v)
            else:
                result[k] = v
        return result
    if isinstance(base, list) and isinstance(override, list):
        elems = base + override
        if elems and all(isinstance(x, dict) for x in elems):
            # 列表元素为字典时，按共有键（优先 name，其次 label）逐元素合并，
            # 以便保留嵌套在 monitors[].actions[] 等列表里用户新增的键。
            for key in ("name", "label"):
                if all(key in x for x in elems):
                    by_key = {x.get(key): x for x in base}
                    result = list(base)
                    for o in override:
                        kk = o.get(key)
                        if kk in by_key:
                            idx = result.index(by_key[kk])
                            result[idx] = _deep_merge(by_key[kk], o)
                        else:
                            result.append(o)
                    return result
        return override
    return override


# ---------------------------------------------------------------------------
# 版本与更新（GitHub Releases 作为更新源）
# ---------------------------------------------------------------------------
# 单一版本来源：同目录 version.txt（如 "1.0.0" 或 "v1.0.0"）。
# 跨平台预留：release 资产按平台关键词匹配（windows / macos / linux），
# 本期仅实现 Windows；其余平台可在 select_update_asset 中扩展。

def parse_version(s):
    """将版本字符串解析为 (major, minor, patch) 三元组，便于比较。"""
    s = (s or "").lstrip("vV").strip()
    parts = []
    for tok in re.split(r"[.\-+]", s):
        if tok.isdigit():
            parts.append(int(tok))
        elif tok == "":
            continue
        else:
            break
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def version_to_str(v):
    return ".".join(map(str, v))


def get_app_version():
    """读取内置版本号；缺失时返回 (0,0,0)。资源(version.txt)在冻结态位于 _MEIPASS。"""
    try:
        p = RES_DIR / "version.txt"
        if p.exists():
            return parse_version(p.read_text(encoding="utf-8").strip())
    except Exception:
        pass
    return (0, 0, 0)


def get_update_repo(cfg):
    """GitHub 仓库 owner/repo；未配置时使用占位值（需用户在 config 填写）。"""
    da = cfg.get("desktop_aipet", {}) if isinstance(cfg, dict) else {}
    return da.get("update_repo") or DEFAULT_UPDATE_REPO


def select_update_asset(assets, platform=None):
    """按当前平台挑选 release 资产。跨平台预留，本期仅 Windows。

    同平台可能同时挂了安装包（.msi/.exe）与升级用代码包（.zip），
    优先选择 .zip 作为「覆盖升级」资产，避免误下载安装包。
    """
    platform = (platform or sys.platform or "").lower()
    if platform.startswith("win"):
        kws = ("windows", "win")
    elif platform.startswith("darwin"):
        kws = ("macos", "mac", "darwin")
    elif platform.startswith("linux"):
        kws = ("linux",)
    else:
        kws = ()
    matched = []
    for a in assets:
        name = (a.get("name") or "").lower()
        if any(kw in name for kw in kws):
            matched.append(a)
    if matched:
        for a in matched:
            if (a.get("name") or "").lower().endswith(".zip"):
                return a
        return matched[0]
    for a in assets:
        if (a.get("name") or "").lower().endswith(".zip"):
            return a
    return assets[0] if assets else None


# ---------------------------------------------------------------------------
# Status palette — 仅用于菜单标题 emoji（托盘/桌宠图标不再叠加状态圆点）
# ---------------------------------------------------------------------------
STATUS_FILL = {"normal": "#22c55e", "warning": "#f59e0b", "error": "#ef4444"}
STATUS_EMOJI = {"normal": "🟢", "warning": "🟡", "error": "🔴"}

# ---------------------------------------------------------------------------
# 桌宠双击随机台词 —— 业务台词全部来自配置文件 config.json 的 "greetings" 字段，
# 不在源码内置。下面仅为「配置缺失/为空」时的兜底文案（请勿在此扩充业务台词）。
# ---------------------------------------------------------------------------
_DEFAULT_GREETINGS = [
    "需要我帮你做什么？",
    "今天心情怎么样？"
]


# ---------------------------------------------------------------------------
# Pet Image Generator — Robot Theme (中性机器人)
# ---------------------------------------------------------------------------

# 机器人本体固定中性配色，不随服务状态变色。
ROBOT_NEUTRAL = {
    "body": "#37474f", "screen": "#26c6da", "eyes": "#e0f7fa",
    "antenna": "#ffd54f", "arm": "#546e7a", "accent": "#4dd0e1",
}


def gen_robot_frame(state, size, colors, frame_idx=0, expression=None):
    """绘制一帧机器人。frame_idx: 0=基础帧, 1=呼吸帧。
    expression: 仅对中性机器人生效，可选 'happy'(弯眼大笑) / 'blink'(闭眼)。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = colors[state]

    m = size // 10
    cx, cy = size // 2, size // 2

    bw, bh = size * 0.55, size * 0.52
    bx0, by0 = cx - bw / 2, cy - bh / 2 + m
    bx1, by1 = cx + bw / 2, cy + bh / 2 + m

    if frame_idx == 1 and state == "normal":
        by0 -= 1
        by1 -= 1

    d.rounded_rectangle([bx0, by0, bx1, by1], radius=m, fill=c["body"],
                        outline="#212121", width=2)

    sw, sh = bw * 0.65, bh * 0.5
    sx0, sy0 = cx - sw / 2, by0 + m * 1.2
    sx1, sy1 = cx + sw / 2, sy0 + sh
    d.rounded_rectangle([sx0, sy0, sx1, sy1], radius=m // 2,
                        fill=c["screen"], outline="#212121", width=2)

    eye_w = sh * 0.30
    eye_h = sh * 0.45 if state != "error" else sh * 0.35
    eye_y = sy0 + sh * 0.28
    eye_gap = sw * 0.22

    expr = expression if (expression and state == "normal") else state

    for ex in [cx - eye_gap, cx + eye_gap]:
        if expr == "happy":
            # 开心的弯弯眼 (^_^)
            d.arc([ex - eye_w / 2, eye_y, ex + eye_w / 2, eye_y + eye_h],
                  200, 340, fill=c["eyes"], width=3)
        elif expr == "blink":
            # 闭眼（细横线）
            d.line([ex - eye_w / 2, eye_y + eye_h / 2,
                    ex + eye_w / 2, eye_y + eye_h / 2],
                   fill=c["body"], width=3)
        elif state == "normal":
            d.rounded_rectangle([ex - eye_w / 2, eye_y, ex + eye_w / 2, eye_y + eye_h],
                                radius=2, fill=c["eyes"])
            d.rounded_rectangle([ex - eye_w / 4, eye_y + 2, ex + eye_w / 4, eye_y + eye_h - 2],
                                radius=1, fill=c["body"])
        elif state == "warning":
            d.rounded_rectangle([ex - eye_w / 2, eye_y, ex + eye_w / 2, eye_y + eye_h],
                                radius=2, fill=c["eyes"])
            d.line([ex - eye_w / 3, eye_y + eye_h * 0.3, ex + eye_w / 3, eye_y + eye_h * 0.7],
                   fill=c["body"], width=2)
        else:
            d.line([ex - eye_w / 2 + 1, eye_y, ex + eye_w / 2 - 1, eye_y + eye_h],
                   fill="#ff1744", width=3)
            d.line([ex + eye_w / 2 - 1, eye_y, ex - eye_w / 2 + 1, eye_y + eye_h],
                   fill="#ff1744", width=3)

    mouth_y = sy0 + sh * 0.72
    mouth_w = sw * 0.25
    if expr == "happy":
        # 大笑嘴（上扬弧线）
        d.arc([cx - mouth_w, mouth_y - m, cx + mouth_w, mouth_y + m * 1.4],
              20, 160, fill=c["accent"], width=3)
    elif state == "normal":
        d.arc([cx - mouth_w, mouth_y - m, cx + mouth_w, mouth_y + m * 0.8],
              220, 320, fill=c["accent"], width=2)
        for i, mx in enumerate([cx - mouth_w - m, cx + mouth_w + m]):
            if frame_idx == int(i == 0):
                d.rectangle([mx, mouth_y - 2, mx + 3, mouth_y + 2], fill=c["accent"])
    elif state == "warning":
        d.arc([cx - mouth_w, mouth_y + m * 0.3, cx + mouth_w, mouth_y + m * 1.8],
              30, 150, fill=c["accent"], width=2)
        d.rectangle([cx - 2, mouth_y - m * 0.5, cx + 2, mouth_y + m * 0.5], fill="#ff8a65")
    else:
        d.rounded_rectangle([cx - mouth_w * 0.6, mouth_y - 2,
                             cx + mouth_w * 0.6, mouth_y + m],
                            radius=2, fill="#ff1744")

    ant_top = by0 - m * 1.5
    d.line([cx, by0 - 2, cx, ant_top], fill=c["body"], width=3)
    ball_size = m * 1.2
    # 天线灯保持机器人主题色，不显示服务状态
    d.ellipse([cx - ball_size / 2, ant_top - ball_size,
               cx + ball_size / 2, ant_top],
              fill=c["antenna"], outline=c["body"], width=1)

    arm_w = bw * 0.12
    arm_h = bh * 0.5
    for sign in (-1, 1):
        if sign > 0:
            ax0 = cx + bw / 2 - 2
        else:
            ax0 = cx - bw / 2 - arm_w + 2
        ax1 = ax0 + arm_w
        arm_y0 = by0 + bh * 0.15
        d.rounded_rectangle([ax0, arm_y0, ax1, arm_y0 + arm_h],
                            radius=3, fill=c["arm"], outline="#212121", width=2)
        hx = (ax0 + ax1) / 2
        d.ellipse([hx - m * 0.5, arm_y0 + arm_h - 2,
                   hx + m * 0.5, arm_y0 + arm_h + m * 0.6],
                  fill=c["accent"], outline="#212121", width=1)

    leg_w = bw * 0.18
    leg_h = m * 1.5
    for lx in [cx - bw * 0.3, cx + bw * 0.3]:
        d.rounded_rectangle([lx - leg_w / 2, by1 - 2, lx + leg_w / 2, by1 + leg_h],
                            radius=3, fill=c["arm"], outline="#212121", width=2)

    return img


def _load_robot_frames_from_pets(size):
    """robot 主题直接加载 pets/robot 图片帧，使其与 normal_1.png 等视觉一致。

    图片缺失（或 normal 帧不存在）时返回 None，由调用方回退到代码绘制。
    """
    d = PETS_DIR / "robot"
    if not d.is_dir():
        return None

    def load(state):
        imgs = []
        for i in (0, 1):
            p = d / f"{state}_{i}.png"
            if p.exists():
                im = Image.open(p).convert("RGBA")
                im = im.resize((size, size), Image.LANCZOS)
                imgs.append(im)
        return imgs

    out = {s: load(s) for s in ("normal", "warning", "error", "happy", "blink")}
    if not out.get("normal"):
        return None
    # 缺失状态用 normal 帧兜底，保证动画键集合完整
    for s in ("warning", "error", "happy", "blink"):
        if not out.get(s):
            out[s] = out["normal"]
    return out


def generate_pet_frames(scale=1.0, character="robot"):
    """生成桌宠各状态动画帧。返回 {state: [PIL, ...]}。

    保留 normal/warning/error/happy/blink 键以兼容现有动画系统；
    所有角色本体保持中性（不随服务状态变色），warning/error 仅作占位。
    robot 主题优先使用 pets/robot 下的图片资源（与 normal_1.png 一致），
    其余主题沿用代码绘制。
    """
    size = int(72 * scale)
    if character == "robot":
        robot_frames = _load_robot_frames_from_pets(size)
        if robot_frames:
            return robot_frames
    frames = {}
    for state in ("normal", "warning", "error"):
        frames[state] = [
            gen_pet_frame(character, "normal", size, 0),
            gen_pet_frame(character, "normal", size, 1),
        ]
    frames["happy"] = [
        gen_pet_frame(character, "normal", size, 0, "happy"),
        gen_pet_frame(character, "normal", size, 1, "happy"),
    ]
    frames["blink"] = [
        gen_pet_frame(character, "normal", size, 0, "blink"),
        gen_pet_frame(character, "normal", size, 1, "blink"),
    ]
    return frames


# ---------------------------------------------------------------------------
# 角色绘制分发（通用表情 + 各角色轮廓）
# ---------------------------------------------------------------------------

# 菜单「切换助理」按此顺序循环；CHARACTER_INFO 提供展示名与 emoji
CHARACTER_ORDER = ["robot", "labrador", "bluecat", "piggy", "bunny", "wukong", "pony"]
CHARACTER_INFO = {
    "robot":    {"name": "机器人", "emoji": "\U0001F916"},
    "labrador": {"name": "拉布拉多", "emoji": "\U0001F436"},
    "bluecat":  {"name": "蓝猫", "emoji": "\U0001F431"},
    "piggy":    {"name": "小猪", "emoji": "\U0001F437"},
    "bunny":    {"name": "小白兔", "emoji": "\U0001F430"},
    "wukong":   {"name": "孙悟空", "emoji": "\U0001F412"},
    "pony":     {"name": "小马", "emoji": "\U0001F434"},
}


def _face_eyes(d, expr, cx, eye_y, gap, ew, eh, eye_color, outline):
    """绘制双眼；expr ∈ happy/blink/normal（warning/error 也按 normal 处理，
    保持桌宠中性、不随服务状态变色）。"""
    ow = max(2, int(eh * 0.28))
    for ex in (cx - gap, cx + gap):
        if expr == "happy":
            d.arc([ex - ew / 2, eye_y, ex + ew / 2, eye_y + eh],
                  200, 340, fill=eye_color, width=ow)
        elif expr == "blink":
            d.line([ex - ew / 2, eye_y + eh / 2, ex + ew / 2, eye_y + eh / 2],
                   fill=outline, width=max(2, int(eh * 0.3)))
        else:  # normal / warning / error 均保持中性
            d.ellipse([ex - ew / 2, eye_y, ex + ew / 2, eye_y + eh],
                      fill=eye_color, outline=outline, width=1)


def _face_mouth(d, expr, cx, my, mw, color):
    if expr == "happy":
        d.arc([cx - mw, my, cx + mw, my + mw * 1.2], 20, 160, fill=color, width=2)
    elif expr == "blink":
        d.arc([cx - mw, my, cx + mw, my + mw], 220, 320, fill=color, width=2)
    else:
        d.arc([cx - mw, my, cx + mw, my + mw * 0.7], 220, 320, fill=color, width=2)


def gen_dog_frame(state, size, frame_idx=0, expression=None):
    """拉布拉多犬：垂耳 + 口鼻 + 坐姿身体 + 前爪 + 摇尾。"""
    S = size
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    C = dict(fur="#c89b5a", ear="#9c7636", belly="#ecd9b0",
             nose="#2e2118", eye="#2b2b2b", ol="#6d4c41")
    cx = S // 2
    bob = 1 if frame_idx == 1 else 0
    # 身体（坐姿）
    bt, bb = S * 0.52 + bob, S * 0.88 + bob
    bw = S * 0.52
    d.rounded_rectangle([cx - bw / 2, bt, cx + bw / 2, bb], radius=S * 0.20,
                        fill=C["fur"], outline=C["ol"], width=2)
    d.ellipse([cx - S * 0.17, bt + S * 0.07, cx + S * 0.17, bb - S * 0.03],
              fill=C["belly"])
    # 前爪
    for sx in (-1, 1):
        px = cx + sx * S * 0.14
        d.ellipse([px - S * 0.075, bb - S * 0.07, px + S * 0.075, bb + S * 0.05],
                  fill=C["fur"], outline=C["ol"], width=2)
    # 尾巴（翘起摇动）
    d.line([cx + bw / 2 - 2, bt + S * 0.12, cx + S * 0.48, bt - S * 0.04],
           fill=C["fur"], width=int(S * 0.07))
    d.ellipse([cx + S * 0.44, bt - S * 0.10, cx + S * 0.52, bt - S * 0.02],
              fill=C["fur"], outline=C["ol"], width=2)
    # 头 + 垂耳
    hcy = S * 0.34 + bob
    R = S * 0.27
    d.ellipse([cx - R * 1.20, hcy - R * 0.1, cx - R * 0.5, hcy + R * 1.2],
              fill=C["ear"], outline=C["ol"], width=2)
    d.ellipse([cx + R * 0.5, hcy - R * 0.1, cx + R * 1.20, hcy + R * 1.2],
              fill=C["ear"], outline=C["ol"], width=2)
    d.ellipse([cx - R, hcy - R, cx + R, hcy + R], fill=C["fur"],
              outline=C["ol"], width=2)
    # 口鼻（浅色凸起 + 黑鼻头）
    mu = S * 0.16
    d.ellipse([cx - mu, hcy + R * 0.30, cx + mu, hcy + R * 0.95], fill=C["belly"],
              outline=C["ol"], width=1)
    d.ellipse([cx - S * 0.055, hcy + R * 0.32, cx + S * 0.055, hcy + R * 0.5],
              fill=C["nose"], outline="#000000", width=1)
    d.line([cx, hcy + R * 0.5, cx, hcy + R * 0.6], fill=C["ol"], width=2)
    expr = expression if expression in ("happy", "blink") else "normal"
    _face_eyes(d, expr, cx, hcy - R * 0.05, S * 0.12, S * 0.095, S * 0.12,
               C["eye"], C["ol"])
    _face_mouth(d, expr, cx, hcy + R * 0.6, S * 0.055, C["ol"])
    return img


def gen_cat_frame(state, size, frame_idx=0, expression=None):
    """蓝猫：尖耳 + 蓝灰毛 + 胡须 + 坐姿身体 + 卷尾。"""
    S = size
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    C = dict(fur="#6b7fb0", ear="#4a5a96", belly="#c5cae9",
             nose="#f48fb1", eye="#ffe14d", ol="#283593")
    cx = S // 2
    bob = 1 if frame_idx == 1 else 0
    # 尾巴（上卷到身侧）
    d.line([cx + S * 0.25, S * 0.82 + bob, cx + S * 0.49, S * 0.55 + bob,
            cx + S * 0.40, S * 0.42 + bob], fill=C["fur"],
           width=int(S * 0.08), joint="curve")
    # 身体
    bt, bb = S * 0.52 + bob, S * 0.88 + bob
    bw = S * 0.50
    d.rounded_rectangle([cx - bw / 2, bt, cx + bw / 2, bb], radius=S * 0.20,
                        fill=C["fur"], outline=C["ol"], width=2)
    d.ellipse([cx - S * 0.16, bt + S * 0.07, cx + S * 0.16, bb - S * 0.03],
              fill=C["belly"])
    for sx in (-1, 1):
        px = cx + sx * S * 0.13
        d.ellipse([px - S * 0.07, bb - S * 0.06, px + S * 0.07, bb + S * 0.05],
                  fill=C["fur"], outline=C["ol"], width=2)
    # 头 + 尖耳
    hcy = S * 0.34 + bob
    R = S * 0.27
    d.polygon([(cx - R * 1.05, hcy - R * 0.55), (cx - R * 0.35, hcy - R * 0.95),
               (cx - R * 0.15, hcy - R * 0.45)], fill=C["ear"], outline=C["ol"])
    d.polygon([(cx + R * 1.05, hcy - R * 0.55), (cx + R * 0.35, hcy - R * 0.95),
               (cx + R * 0.15, hcy - R * 0.45)], fill=C["ear"], outline=C["ol"])
    d.ellipse([cx - R, hcy - R, cx + R, hcy + R], fill=C["fur"],
              outline=C["ol"], width=2)
    # 小口鼻
    d.polygon([(cx - S * 0.04, hcy + R * 0.35), (cx + S * 0.04, hcy + R * 0.35),
               (cx, hcy + R * 0.5)], fill=C["nose"])
    d.line([cx, hcy + R * 0.5, cx, hcy + R * 0.6], fill=C["ol"], width=2)
    # 胡须
    for dy in (-S * 0.01, S * 0.04):
        d.line([cx - S * 0.10, hcy + R * 0.42 + dy, cx - S * 0.36, hcy + R * 0.34 + dy],
               fill=C["ol"], width=1)
        d.line([cx + S * 0.10, hcy + R * 0.42 + dy, cx + S * 0.36, hcy + R * 0.34 + dy],
               fill=C["ol"], width=1)
    expr = expression if expression in ("happy", "blink") else "normal"
    _face_eyes(d, expr, cx, hcy - R * 0.05, S * 0.12, S * 0.10, S * 0.12,
               C["eye"], C["ol"])
    _face_mouth(d, expr, cx, hcy + R * 0.6, S * 0.055, C["ol"])
    return img


def gen_pig_frame(state, size, frame_idx=0, expression=None):
    """小猪：小三角耳 + 大猪鼻（双鼻孔）+ 圆身 + 四蹄 + 卷尾。"""
    S = size
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    C = dict(fur="#f8b9c4", ear="#f48fb1", snout="#f48fb1",
             nose="#c2185b", eye="#3e2723", ol="#d81b60", hoof="#f06292")
    cx = S // 2
    bob = 1 if frame_idx == 1 else 0
    # 卷尾
    d.line([cx + S * 0.26, S * 0.62 + bob, cx + S * 0.42, S * 0.55 + bob,
            cx + S * 0.34, S * 0.46 + bob], fill=C["fur"],
           width=int(S * 0.05), joint="curve")
    # 身体
    bt, bb = S * 0.50 + bob, S * 0.88 + bob
    bw = S * 0.54
    d.rounded_rectangle([cx - bw / 2, bt, cx + bw / 2, bb], radius=S * 0.22,
                        fill=C["fur"], outline=C["ol"], width=2)
    d.ellipse([cx - S * 0.18, bt + S * 0.06, cx + S * 0.18, bb - S * 0.02],
              fill=C["snout"])
    # 四蹄（前后各一对外露）
    for sx in (-1, 1):
        px = cx + sx * S * 0.16
        d.ellipse([px - S * 0.08, bb - S * 0.05, px + S * 0.08, bb + S * 0.06],
                  fill=C["hoof"], outline=C["ol"], width=2)
    # 头 + 小三角耳
    hcy = S * 0.33 + bob
    R = S * 0.27
    d.polygon([(cx - R * 1.0, hcy - R * 0.5), (cx - R * 0.6, hcy - R * 0.85),
               (cx - R * 0.45, hcy - R * 0.35)], fill=C["ear"], outline=C["ol"])
    d.polygon([(cx + R * 1.0, hcy - R * 0.5), (cx + R * 0.6, hcy - R * 0.85),
               (cx + R * 0.45, hcy - R * 0.35)], fill=C["ear"], outline=C["ol"])
    d.ellipse([cx - R, hcy - R, cx + R, hcy + R], fill=C["fur"],
              outline=C["ol"], width=2)
    # 大猪鼻（双鼻孔）
    sn = S * 0.20
    d.ellipse([cx - sn, hcy + R * 0.30, cx + sn, hcy + R * 0.95], fill=C["snout"],
              outline=C["ol"], width=2)
    d.ellipse([cx - S * 0.075, hcy + R * 0.45, cx - S * 0.02, hcy + R * 0.62],
              fill=C["nose"])
    d.ellipse([cx + S * 0.02, hcy + R * 0.45, cx + S * 0.075, hcy + R * 0.62],
              fill=C["nose"])
    expr = expression if expression in ("happy", "blink") else "normal"
    _face_eyes(d, expr, cx, hcy - R * 0.1, S * 0.12, S * 0.10, S * 0.11,
               C["eye"], C["ol"])
    _face_mouth(d, expr, cx, hcy + R * 0.95, S * 0.05, C["ol"])
    return img


def gen_bunny_frame(state, size, frame_idx=0, expression=None):
    """小白兔：长耳（完整入画）+ 圆身 + 大后脚 + 小前爪 + 绒尾。"""
    S = size
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    C = dict(fur="#fafafa", inner="#f8bbd0", nose="#f48fb1",
             eye="#5d4037", ol="#bdbdbd", cheek="#f8bbd0", tail="#ffffff")
    cx = S // 2
    bob = 1 if frame_idx == 1 else 0
    # 绒尾
    d.ellipse([cx + S * 0.22, S * 0.60 + bob, cx + S * 0.40, S * 0.78 + bob],
              fill=C["tail"], outline=C["ol"], width=2)
    # 身体
    bt, bb = S * 0.54 + bob, S * 0.92 + bob
    bw = S * 0.46
    d.rounded_rectangle([cx - bw / 2, bt, cx + bw / 2, bb], radius=S * 0.20,
                        fill=C["fur"], outline=C["ol"], width=2)
    # 大后脚（扁椭圆）
    for sx in (-1, 1):
        px = cx + sx * S * 0.16
        d.ellipse([px - S * 0.12, bb - S * 0.03, px + S * 0.12, bb + S * 0.08],
                  fill=C["fur"], outline=C["ol"], width=2)
    # 小前爪
    for sx in (-1, 1):
        px = cx + sx * S * 0.075
        d.ellipse([px - S * 0.05, bb - S * 0.13, px + S * 0.05, bb - S * 0.02],
                  fill=C["fur"], outline=C["ol"], width=1)
    # 头
    hcy = S * 0.42 + bob
    R = S * 0.24
    # 长耳：从画布顶部延伸到头部上方，左右各一，完整可见
    for sx in (-1, 1):
        ex = cx + sx * S * 0.13
        d.ellipse([ex - S * 0.085, S * 0.02 + bob, ex + S * 0.085, hcy + bob],
                  fill=C["fur"], outline=C["ol"], width=2)
        d.ellipse([ex - S * 0.05, S * 0.05 + bob, ex + S * 0.05, hcy - S * 0.03 + bob],
                  fill=C["inner"])
    d.ellipse([cx - R, hcy - R, cx + R, hcy + R], fill=C["fur"],
              outline=C["ol"], width=2)
    # 腮红
    d.ellipse([cx - S * 0.22, hcy + S * 0.02, cx - S * 0.10, hcy + S * 0.10],
              fill=C["cheek"])
    d.ellipse([cx + S * 0.10, hcy + S * 0.02, cx + S * 0.22, hcy + S * 0.10],
              fill=C["cheek"])
    d.polygon([(cx - S * 0.035, hcy + S * 0.02), (cx + S * 0.035, hcy + S * 0.02),
               (cx, hcy + S * 0.07)], fill=C["nose"])
    d.arc([cx - S * 0.09, hcy + S * 0.05, cx + S * 0.09, hcy + S * 0.17],
          20, 160, fill=C["ol"], width=2)
    expr = expression if expression in ("happy", "blink") else "normal"
    _face_eyes(d, expr, cx, hcy - S * 0.06, S * 0.11, S * 0.095, S * 0.12,
               C["eye"], C["ol"])
    _face_mouth(d, expr, cx, hcy + S * 0.07, S * 0.05, C["ol"])
    return img


def gen_wukong_frame(state, size, frame_idx=0, expression=None):
    """孙悟空：棕毛猴（大圆耳 + 桃脸）+ 红金紧箍 + 长尾 + 双手持金箍棒。"""
    S = size
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    C = dict(fur="#8d6e63", ear="#a1887f", face="#ffe0b2",
             band="#e53935", gold="#ffd54f", eye="#3e2723", ol="#4e342e",
             staff="#ffca28", staffband="#c62828")
    cx = S // 2
    bob = 1 if frame_idx == 1 else 0
    # 长尾（卷曲，身后）
    d.line([cx + S * 0.18, S * 0.74 + bob, cx + S * 0.46, S * 0.66 + bob,
            cx + S * 0.40, S * 0.48 + bob, cx + S * 0.54, S * 0.38 + bob],
           fill=C["fur"], width=int(S * 0.05), joint="curve")
    # 身体
    bt, bb = S * 0.56 + bob, S * 0.90 + bob
    bw = S * 0.46
    d.rounded_rectangle([cx - bw / 2, bt, cx + bw / 2, bb], radius=S * 0.20,
                        fill=C["fur"], outline=C["ol"], width=2)
    d.ellipse([cx - S * 0.15, bt + S * 0.05, cx + S * 0.15, bb - S * 0.04],
              fill=C["face"])
    # 头
    hcy = S * 0.32 + bob
    R = S * 0.28
    # 大圆耳（猴子特征）
    for sx in (-1, 1):
        ex = cx + sx * R * 1.0
        d.ellipse([ex - R * 0.52, hcy - R * 0.10, ex + R * 0.52, hcy + R * 0.60],
                  fill=C["fur"], outline=C["ol"], width=2)
        d.ellipse([ex - R * 0.30, hcy - R * 0.0, ex + R * 0.30, hcy + R * 0.45],
                  fill=C["ear"])
    d.ellipse([cx - R, hcy - R, cx + R, hcy + R], fill=C["fur"],
              outline=C["ol"], width=2)
    # 桃脸（浅色脸盘）
    fr = R * 0.84
    d.ellipse([cx - fr, hcy - R * 0.12, cx + fr, hcy + R * 1.08], fill=C["face"],
              outline=C["ol"], width=1)
    # 紧箍（红带 + 金箍）
    band_y = hcy - R * 0.48
    d.rounded_rectangle([cx - R * 0.92, band_y - S * 0.032,
                         cx + R * 0.92, band_y + S * 0.032], radius=2, fill=C["band"])
    d.ellipse([cx - S * 0.07, band_y - S * 0.045, cx + S * 0.07, band_y + S * 0.05],
              fill=C["gold"], outline=C["ol"], width=1)
    # 眼
    expr = expression if expression in ("happy", "blink") else "normal"
    _face_eyes(d, expr, cx, hcy + R * 0.10, S * 0.12, S * 0.09, S * 0.12,
               C["eye"], C["ol"])
    # 鼻 + 嘴（猴子小嘴）
    d.ellipse([cx - S * 0.05, hcy + R * 0.58, cx + S * 0.05, hcy + R * 0.72],
              fill=C["ol"])
    _face_mouth(d, expr, cx, hcy + R * 0.74, S * 0.05, C["ol"])
    # 金箍棒（最后画，在身体右侧斜向上，避免遮挡脸部；右手握持）
    p1 = (cx + S * 0.22, S * 0.72)
    p2 = (cx + S * 0.46, S * 0.10)
    d.line([p1, p2], fill=C["staff"], width=int(S * 0.06))
    # 棒方向单位向量 (0.407, -0.913)，垂直 (0.913, 0.407)
    for t in (0.18, 0.82):
        ax = p1[0] + (p2[0] - p1[0]) * t
        ay = p1[1] + (p2[1] - p1[1]) * t
        ox, oy = S * 0.027, S * 0.012
        d.line([ax - ox, ay - oy, ax + ox, ay + oy], fill=C["staffband"],
               width=int(S * 0.08))
    # 金帽（两端）
    for t in (0.0, 1.0):
        ax = p1[0] + (p2[0] - p1[0]) * t
        ay = p1[1] + (p2[1] - p1[1]) * t
        d.ellipse([ax - S * 0.03, ay - S * 0.03, ax + S * 0.03, ay + S * 0.03],
                  fill=C["gold"], outline=C["ol"], width=1)
    # 右手握棒
    d.ellipse([p1[0] - S * 0.07, p1[1] - S * 0.07,
               p1[0] + S * 0.07, p1[1] + S * 0.07],
              fill=C["fur"], outline=C["ol"], width=2)
    # 左手自然下垂在身体左侧
    d.ellipse([cx - S * 0.27, S * 0.64 + bob,
               cx - S * 0.15, S * 0.76 + bob],
              fill=C["fur"], outline=C["ol"], width=2)
    return img


def gen_pony_frame(state, size, frame_idx=0, expression=None):
    """小马（矮种马）：长脸 + 口鼻 + 尖耳 + 鬃毛 + 四腿 + 飘逸长尾。"""
    S = size
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    C = dict(fur="#e0b07a", mane="#8d5524", muzzle="#f0d2a8",
             hoof="#5d4037", eye="#2b2b2b", ol="#6d4c41")
    cx = S // 2
    bob = 1 if frame_idx == 1 else 0
    # 长尾（飘逸）
    d.line([cx + S * 0.22, S * 0.56 + bob, cx + S * 0.44, S * 0.66 + bob,
            cx + S * 0.38, S * 0.84 + bob], fill=C["mane"],
           width=int(S * 0.06), joint="curve")
    # 身体
    bt, bb = S * 0.54 + bob, S * 0.86 + bob
    bw = S * 0.50
    d.rounded_rectangle([cx - bw / 2, bt, cx + bw / 2, bb], radius=S * 0.18,
                        fill=C["fur"], outline=C["ol"], width=2)
    # 四腿 + 蹄
    for sx in (-1, 1):
        for lx in (sx * S * 0.15, sx * S * 0.29):
            d.rounded_rectangle([cx + lx - S * 0.045, bb - S * 0.03,
                                 cx + lx + S * 0.045, bb + S * 0.10],
                                radius=2, fill=C["fur"], outline=C["ol"], width=2)
            d.ellipse([cx + lx - S * 0.05, bb + S * 0.07, cx + lx + S * 0.05,
                       bb + S * 0.12], fill=C["hoof"])
    # 颈
    d.polygon([cx - S * 0.16, bt, cx + S * 0.16, bt,
               cx + S * 0.12, S * 0.40, cx - S * 0.12, S * 0.40],
              fill=C["fur"], outline=C["ol"], width=2)
    # 头（圆颅 + 向下长口鼻）
    hcy = S * 0.30 + bob
    R = S * 0.22
    d.polygon([(cx - R * 0.7, hcy - R * 0.7), (cx - R * 0.4, hcy - R * 1.15),
               (cx - R * 0.15, hcy - R * 0.6)], fill=C["fur"], outline=C["ol"])
    d.polygon([(cx + R * 0.7, hcy - R * 0.7), (cx + R * 0.4, hcy - R * 1.15),
               (cx + R * 0.15, hcy - R * 0.6)], fill=C["fur"], outline=C["ol"])
    d.polygon([(cx - R * 0.9, hcy - R * 0.4), (cx - R * 0.3, hcy - R * 1.05),
               (cx + R * 0.15, hcy - R * 0.9), (cx - R * 0.1, hcy - R * 0.3)],
              fill=C["mane"], outline=C["ol"])
    d.ellipse([cx - R, hcy - R, cx + R, hcy + R], fill=C["fur"],
              outline=C["ol"], width=2)
    mz = S * 0.14
    d.ellipse([cx - mz, hcy + R * 0.45, cx + mz, hcy + R * 1.25], fill=C["muzzle"],
              outline=C["ol"], width=1)
    d.ellipse([cx - S * 0.06, hcy + R * 0.85, cx - S * 0.015, hcy + R * 1.05],
              fill=C["ol"])
    d.ellipse([cx + S * 0.015, hcy + R * 0.85, cx + S * 0.06, hcy + R * 1.05],
              fill=C["ol"])
    expr = expression if expression in ("happy", "blink") else "normal"
    _face_eyes(d, expr, cx, hcy - R * 0.0, S * 0.11, S * 0.09, S * 0.11,
               C["eye"], C["ol"])
    return img


def gen_pet_frame(character, state, size, frame_idx=0, expression=None):
    """按角色分发到对应绘制函数；未知角色回退到机器人。"""
    fn = {
        "robot":    lambda: gen_robot_frame(state, size, {"normal": ROBOT_NEUTRAL},
                                            frame_idx, expression),
        "labrador": lambda: gen_dog_frame(state, size, frame_idx, expression),
        "bluecat":  lambda: gen_cat_frame(state, size, frame_idx, expression),
        "piggy":    lambda: gen_pig_frame(state, size, frame_idx, expression),
        "bunny":    lambda: gen_bunny_frame(state, size, frame_idx, expression),
        "wukong":   lambda: gen_wukong_frame(state, size, frame_idx, expression),
        "pony":     lambda: gen_pony_frame(state, size, frame_idx, expression),
    }.get(character, lambda: gen_robot_frame(
        state, size, {"normal": ROBOT_NEUTRAL}, frame_idx, expression))
    return fn()


def generate_tray_icons(character="robot"):
    """生成托盘图标（与当前桌宠形象一致）。返回 {state: PIL Image}。"""
    ICONS_DIR.mkdir(exist_ok=True)
    result = {}
    for state in ("normal", "warning", "error"):
        # robot 主题直接用 normal_1.png（与运行时桌宠形象一致）；其余走代码绘制
        if character == "robot":
            p = PETS_DIR / "robot" / "normal_1.png"
            if p.exists():
                img = Image.open(p).convert("RGBA").resize((32, 32), Image.LANCZOS)
            else:
                img = gen_pet_frame(character, "normal", 64, 0).resize(
                    (32, 32), Image.LANCZOS)
        else:
            img = gen_pet_frame(character, "normal", 64, 0).resize(
                (32, 32), Image.LANCZOS)
        path = ICONS_DIR / f"{character}_{state}.ico"
        try:
            img.save(str(path), format="ICO", sizes=[(32, 32), (16, 16)])
        except Exception as e:  # 图标落盘失败不应阻断启动
            log.warning("写入托盘图标失败 %s: %s", path, e)
        result[state] = img
    return result


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------

try:
    import httpx
except ImportError:
    httpx = None


def check_monitor(monitor):
    name = monitor.get("name", "Unknown")
    endpoint = monitor.get("endpoint", "")
    if not endpoint:
        return name, False, "无端点"

    try:
        if httpx:
            resp = httpx.get(endpoint, timeout=5.0, follow_redirects=True)
            return name, resp.status_code == 200, f"HTTP {resp.status_code}"
        import urllib.request
        resp = urllib.request.urlopen(endpoint, timeout=5)
        return name, resp.status == 200, f"HTTP {resp.status}"
    except Exception as e:
        detail = str(e)[:80].lower()
        if "timed out" in detail or "timeout" in detail:
            detail = "连接超时"
        elif "refused" in detail or "10061" in detail:
            detail = "连接被拒绝"
        elif "getaddrinfo" in detail:
            detail = "域名解析失败"
        return name, False, detail


# ---------------------------------------------------------------------------
# Process launching
# ---------------------------------------------------------------------------

def _ps_quote(s):
    """PowerShell 单引号字符串转义。"""
    return str(s).replace("'", "''")


def build_tee_powershell(target, log_file, append=False, extra_env=None):
    """构造 PowerShell 5.1 命令：执行 target，同时把输出打印到控制台并写入 UTF-8 日志。

    使用 StreamWriter 而非 Tee-Object —— PS 5.1 的 Tee-Object 只能写 UTF-16。
    """
    env_parts = [
        "$env:PYTHONUNBUFFERED='1'",
        "$env:PYTHONIOENCODING='utf-8'",
    ]
    for k, v in (extra_env or {}).items():
        env_parts.append(f"$env:{k}='{_ps_quote(v)}'")

    parts = [
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new()",
        "$OutputEncoding=[System.Text.UTF8Encoding]::new()",
        *env_parts,
        f"$log='{_ps_quote(log_file)}'",
        "New-Item -ItemType Directory -Path (Split-Path -Parent $log) -Force | Out-Null",
        f"$sw=[System.IO.StreamWriter]::new($log,${str(bool(append)).lower()},"
        "[System.Text.UTF8Encoding]::new($false))",
        "$sw.AutoFlush=$true",
        "$sw.WriteLine('=== ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + "
        f"' 启动 {_ps_quote(os.path.basename(str(target)))} ===')",
        f"try {{ & '{_ps_quote(target)}' 2>&1 | ForEach-Object "
        "{ Write-Host $_; $sw.WriteLine([string]$_) } } "
        "finally { $sw.Flush(); $sw.Close() }",
    ]
    return "; ".join(parts)


def launch(args, visible=True, cwd=None, wait=False, timeout=None):
    """启动进程。visible=True 时新开控制台窗口。"""
    flags = CREATE_NEW_CONSOLE if visible else CREATE_NO_WINDOW
    kwargs = {"creationflags": flags, "cwd": cwd, "close_fds": True}
    if not visible:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    if wait:
        return subprocess.call(args, timeout=timeout, **kwargs)
    return subprocess.Popen(args, **kwargs)


def powershell_args(command, no_exit=True):
    args = ["powershell.exe", "-NoLogo", "-ExecutionPolicy", "Bypass"]
    if no_exit:
        args.append("-NoExit")
    args += ["-Command", command]
    return args


# ---------------------------------------------------------------------------
# Pet Bubble — 悬浮桌宠旁的气泡对话框（替代传统弹出式 Toast）
# ---------------------------------------------------------------------------

BUBBLE_STYLES = {
    "info":  {"bg": "#eff6ff", "border": "#bfdbfe", "title": "#1e40af",
              "msg": "#3b82f6"},
    "ok":    {"bg": "#f0fdf4", "border": "#bbf7d0", "title": "#166534",
              "msg": "#22c55e"},
    "warn":  {"bg": "#fffbeb", "border": "#fde68a", "title": "#92400e",
              "msg": "#f59e0b"},
    "error": {"bg": "#fef2f2", "border": "#fecaca", "title": "#991b1b",
              "msg": "#ef4444"},
}


class PetBubble:
    """附着在桌面宠物旁的气泡通知。

    - 单实例，重复调用会更新内容并重置自动隐藏计时器。
    - 宠物可见时出现在宠物上方（水平居中），超出屏幕上边缘则翻转到宠物下方。
    - 宠物隐藏时退化为屏幕右下角气泡。
    - 点击气泡立即关闭。
    """

    def __init__(self, app):
        self.app = app
        self.root = app.root
        self._win = None
        self._hide_id = None
        self._photo = None

    def show(self, title, message="", level="info", duration=4000):
        try:
            self._show(title, message, level, duration)
        except Exception:
            log.exception("气泡显示失败")

    def hide(self):
        if self._hide_id and self.root and self.root.winfo_exists():
            try:
                self.root.after_cancel(self._hide_id)
            except Exception:
                pass
        self._hide_id = None
        if self._win and self._win.winfo_exists():
            try:
                self._win.destroy()
            except Exception:
                pass
        self._win = None

    def _show(self, title, message, level, duration):
        style = BUBBLE_STYLES.get(level, BUBBLE_STYLES["info"])
        if self._win is None or not self._win.winfo_exists():
            self._win = tk.Toplevel(self.root)
            self._win.overrideredirect(True)
            self._win.attributes("-topmost", True)
            self._win.configure(bg=TRANSPARENT_COLOR)
        win = self._win
        win.lift()

        # 清空旧内容
        for w in list(win.winfo_children()):
            w.destroy()

        pad = 12
        max_w = 280
        title_font = ("Microsoft YaHei UI", 10, "bold")
        msg_font = ("Microsoft YaHei UI", 9)

        # 先创建隐藏文本测量尺寸
        tmp = tk.Canvas(win, bg=TRANSPARENT_COLOR, highlightthickness=0)
        tmp.pack()
        tid = tmp.create_text(0, 0, text=title, font=title_font, anchor="nw")
        mid = tmp.create_text(0, 0, text=message, font=msg_font,
                              anchor="nw", width=max_w - pad * 2)
        tmp.update_idletasks()
        tb = tmp.bbox(tid)
        mb = tmp.bbox(mid) if message else (0, 0, 0, 0)
        title_w, title_h = tb[2] - tb[0], tb[3] - tb[1]
        msg_w = mb[2] - mb[0] if message else 0
        msg_h = mb[3] - mb[1] if message else 0
        tmp.destroy()

        body_w = max(title_w, msg_w) + pad * 2
        body_h = title_h + (8 if message else 0) + msg_h + pad * 2
        body_w = max(body_w, 140)
        body_h = max(body_h, 44)
        tail_h = 10
        tail_w = 16
        radius = 14
        total_w = body_w
        total_h = body_h + tail_h

        # 绘制气泡背景图
        img = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)

        # 判断是否能放在宠物上方
        pet_visible = (self.root is not None and self.root.winfo_exists()
                       and self.root.state() == "normal")
        px, py, pw, ph = self._pet_geometry()
        place_above = True
        if pet_visible:
            bx = px + pw // 2 - total_w // 2
            by = py - total_h - 4
            if by < 0:
                place_above = False
                by = py + ph + 4
        else:
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            bx = sw - total_w - 20
            by = sh - total_h - 70
            place_above = False

        # 画圆角矩形主体；尾巴方向根据放置位置调整
        if place_above:
            d.rounded_rectangle([0, 0, body_w - 1, body_h - 1],
                                radius=radius, fill=style["bg"],
                                outline=style["border"], width=1)
            d.polygon([
                (body_w // 2 - tail_w // 2, body_h - 1),
                (body_w // 2 + tail_w // 2, body_h - 1),
                (body_w // 2, total_h - 1)
            ], fill=style["bg"], outline=style["border"])
            # 覆盖尾巴与矩形交界处的内部边框线，避免重边
            d.line([
                (body_w // 2 - tail_w // 2 + 1, body_h - 1),
                (body_w // 2 + tail_w // 2 - 1, body_h - 1)
            ], fill=style["bg"], width=2)
            text_offset_y = 0
        else:
            d.rounded_rectangle([0, tail_h, body_w - 1, total_h - 1],
                                radius=radius, fill=style["bg"],
                                outline=style["border"], width=1)
            d.polygon([
                (body_w // 2 - tail_w // 2, tail_h),
                (body_w // 2 + tail_w // 2, tail_h),
                (body_w // 2, 0)
            ], fill=style["bg"], outline=style["border"])
            d.line([
                (body_w // 2 - tail_w // 2 + 1, tail_h),
                (body_w // 2 + tail_w // 2 - 1, tail_h)
            ], fill=style["bg"], width=2)
            text_offset_y = tail_h

        self._photo = ImageTk.PhotoImage(img)

        canvas = tk.Canvas(win, width=total_w, height=total_h,
                           bg=TRANSPARENT_COLOR, highlightthickness=0)
        canvas.pack()
        canvas.create_image(0, 0, image=self._photo, anchor="nw")
        canvas.create_text(pad, pad + text_offset_y, text=title,
                           font=title_font, fill=style["title"], anchor="nw")
        if message:
            canvas.create_text(pad, pad + text_offset_y + title_h + 8,
                               text=message, font=msg_font, fill=style["msg"],
                               anchor="nw", width=max_w - pad * 2)

        # 水平方向尽量居中；边界限制在屏幕内
        if pet_visible:
            bx = px + pw // 2 - total_w // 2
        bx = max(8, min(bx, win.winfo_screenwidth() - total_w - 8))
        win.geometry(f"{total_w}x{total_h}+{bx}+{by}")

        # 透明背景
        try:
            win.attributes("-transparentcolor", TRANSPARENT_COLOR)
        except Exception:
            pass
        # 气泡也是独立顶层窗口，需同样从任务栏隐藏（必须在 -transparentcolor 之后）
        self.app._hide_from_taskbar(win)

        win.bind("<Button-1>", lambda e: self.hide())

        if self._hide_id and self.root and self.root.winfo_exists():
            try:
                self.root.after_cancel(self._hide_id)
            except Exception:
                pass
        if duration and duration > 0 and self.root and self.root.winfo_exists():
            self._hide_id = self.root.after(duration, self.hide)

    def _pet_geometry(self):
        if self.root and self.root.winfo_exists():
            return (self.root.winfo_x(), self.root.winfo_y(),
                    self.root.winfo_width(), self.root.winfo_height())
        sw = self.root.winfo_screenwidth() if self.root else 1920
        sh = self.root.winfo_screenheight() if self.root else 1080
        size = int(72 * getattr(self.app, "scale", 1.0))
        return (sw - size - 20, sh - size - 60, size, size)


# ---------------------------------------------------------------------------
# Viewer windows
# ---------------------------------------------------------------------------

VIEW_BG = "#1e1e1e"
VIEW_FG = "#d4d4d4"
BAR_BG = "#252526"


def _make_text_area(parent):
    frame = tk.Frame(parent, bg=VIEW_BG)
    frame.pack(fill="both", expand=True)
    ysb = tk.Scrollbar(frame, orient="vertical")
    ysb.pack(side="right", fill="y")
    xsb = tk.Scrollbar(frame, orient="horizontal")
    xsb.pack(side="bottom", fill="x")
    text = tk.Text(frame, wrap="none", font=("Consolas", 10),
                   bg=VIEW_BG, fg=VIEW_FG, insertbackground="#ffffff",
                   selectbackground="#264f78", relief="flat",
                   yscrollcommand=ysb.set, xscrollcommand=xsb.set)
    text.pack(side="left", fill="both", expand=True)
    ysb.config(command=text.yview)
    xsb.config(command=text.xview)
    return text


def _toolbar(parent):
    bar = tk.Frame(parent, bg=BAR_BG, pady=6, padx=8)
    bar.pack(fill="x", side="bottom")
    return bar


def _btn(bar, text, cmd, **kw):
    b = tk.Button(bar, text=text, command=cmd, bg="#3a3d41", fg="#e5e7eb",
                  activebackground="#4b5057", activeforeground="#ffffff",
                  relief="flat", padx=12, pady=3,
                  font=("Microsoft YaHei UI", 9), cursor="hand2", **kw)
    b.pack(side="left", padx=(0, 6))
    return b


class ConfigViewer(tk.Toplevel):
    """文件查看窗口：默认只读，可一键切换为编辑态并保存。"""

    def __init__(self, master, title, filepath, pretty_json=True,
                 editable=False, on_save=None):
        super().__init__(master)
        self.filepath = filepath
        self.pretty = pretty_json
        self._editing = False
        self.on_save = on_save
        self.title(f"{title} — {filepath}")
        self.geometry("900x640")
        self.configure(bg=VIEW_BG)
        self.attributes("-topmost", True)
        self.after(400, lambda: self.attributes("-topmost", False))

        self.text = _make_text_area(self)
        self.bar = _toolbar(self)
        self.reload()
        if editable:
            self._enter_edit()

    def _build_toolbar(self):
        for w in list(self.bar.winfo_children()):
            w.destroy()
        _btn(self.bar, "🔄 重新载入", self.reload)
        if self._editing:
            _btn(self.bar, "💾 保存", self._save)
            _btn(self.bar, "↩ 取消", self._cancel_edit)
        else:
            _btn(self.bar, "✏️ 编辑", self._enter_edit)
        _btn(self.bar, "📂 打开", self.open_external)
        _btn(self.bar, "📋 复制路径", self.copy_path)
        _btn(self.bar, "关闭", self.destroy)
        self.status = tk.Label(self.bar, text="", bg=BAR_BG, fg="#9ca3af",
                               font=("Microsoft YaHei UI", 9))
        self.status.pack(side="right")

    def reload(self):
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        if not self.filepath or not os.path.exists(self.filepath):
            self.text.insert("1.0", f"[文件不存在]\n{self.filepath}")
            self._build_toolbar()
            self.status.config(text="文件不存在")
            self.text.config(state="normal" if self._editing else "disabled")
            return
        try:
            with open(self.filepath, "r", encoding="utf-8-sig",
                      errors="replace") as f:
                content = f.read()
            if self.pretty and self.filepath.lower().endswith(".json"):
                try:
                    content = json.dumps(json.loads(content),
                                         ensure_ascii=False, indent=2)
                except Exception:
                    pass
            self.text.insert("1.0", content)
            size = os.path.getsize(self.filepath)
            mtime = datetime.fromtimestamp(os.path.getmtime(self.filepath))
            self._build_toolbar()
            self.status.config(
                text=f"{size:,} 字节 · 修改于 {mtime:%Y-%m-%d %H:%M:%S}"
                     + (" · 只读" if not self._editing else " · 编辑中"))
        except Exception as e:
            self._build_toolbar()
            self.text.insert("1.0", f"[读取失败] {e}")
            self.status.config(text="读取失败")
        self.text.config(state="normal" if self._editing else "disabled")

    def _enter_edit(self):
        self._editing = True
        self.text.config(state="normal")
        self._build_toolbar()
        self.status.config(text="编辑模式 · 修改后点「保存」")

    def _cancel_edit(self):
        self._editing = False
        self.reload()

    def _save(self):
        try:
            content = self.text.get("1.0", "end-1c")
            if self.pretty and self.filepath.lower().endswith(".json"):
                try:
                    content = json.dumps(json.loads(content),
                                         ensure_ascii=False, indent=2)
                except Exception as e:
                    self.status.config(text=f"JSON 解析失败，未保存: {e}")
                    return
            if not self.filepath:
                self.status.config(text="无文件路径，无法保存")
                return
            with open(self.filepath, "w", encoding="utf-8") as f:
                f.write(content)
            self._editing = False
            self.text.config(state="disabled")
            self._build_toolbar()
            self.status.config(text="已保存 ✓")
            log.info("保存配置: %s", self.filepath)
            if callable(self.on_save):
                self.after(200, self.on_save)  # 延迟确保文件落盘

        except Exception as e:
            log.exception("保存配置失败")
            self.status.config(text=f"保存失败: {e}")

    def open_external(self):
        if self.filepath and os.path.exists(self.filepath):
            os.startfile(self.filepath)

    def copy_path(self):
        self.clipboard_clear()
        self.clipboard_append(self.filepath)
        self.status.config(text="路径已复制")


class LogViewer(tk.Toplevel):
    """实时日志窗口：增量尾随文件，支持自动滚动与关键字过滤。"""

    def __init__(self, master, title, filepath, refresh_ms=1000,
                 tail_lines=800, hint=""):
        super().__init__(master)
        self.filepath = filepath
        self.refresh_ms = max(200, int(refresh_ms))
        self.tail_lines = max(50, int(tail_lines))
        self.hint = hint
        self._pos = 0
        self._inode_size = -1
        self._alive = True
        self._filter = ""

        self.title(f"{title} — {filepath}")
        self.geometry("1000x660")
        self.configure(bg=VIEW_BG)
        self.attributes("-topmost", True)
        self.after(400, lambda: self.attributes("-topmost", False))
        self.protocol("WM_DELETE_WINDOW", self._close)

        top = tk.Frame(self, bg=BAR_BG, pady=6, padx=8)
        top.pack(fill="x", side="top")
        tk.Label(top, text="过滤:", bg=BAR_BG, fg="#9ca3af",
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.filter_var = tk.StringVar()
        ent = tk.Entry(top, textvariable=self.filter_var, bg="#3a3d41",
                       fg="#e5e7eb", insertbackground="#ffffff", relief="flat",
                       font=("Consolas", 10), width=28)
        ent.pack(side="left", padx=6)
        ent.bind("<Return>", lambda e: self._reload_all())
        self.autoscroll = tk.BooleanVar(value=True)
        tk.Checkbutton(top, text="自动滚动", variable=self.autoscroll,
                       bg=BAR_BG, fg="#e5e7eb", selectcolor=BAR_BG,
                       activebackground=BAR_BG, activeforeground="#ffffff",
                       font=("Microsoft YaHei UI", 9)).pack(side="left", padx=8)
        self.status = tk.Label(top, text="", bg=BAR_BG, fg="#9ca3af",
                               font=("Microsoft YaHei UI", 9))
        self.status.pack(side="right")

        self.text = _make_text_area(self)
        self.text.tag_config("err", foreground="#f87171")
        self.text.tag_config("warn", foreground="#fbbf24")
        self.text.tag_config("ok", foreground="#4ade80")
        self.text.tag_config("hint", foreground="#60a5fa")

        bar = _toolbar(self)
        _btn(bar, "🔄 重新载入", self._reload_all)
        _btn(bar, "🧹 清空显示", self._clear_view)
        _btn(bar, "📂 打开日志文件", self._open_external)
        _btn(bar, "关闭", self._close)

        self._reload_all()
        self._tick()

    # -- rendering ---------------------------------------------------
    def _tag_for(self, line):
        low = line.lower()
        if any(k in low for k in ("error", "exception", "traceback",
                                  "critical", "failed", "错误", "失败")):
            return "err"
        if any(k in low for k in ("warn", "警告")):
            return "warn"
        if any(k in low for k in ("started", "running", "success", "ready",
                                  "成功", "启动")):
            return "ok"
        return None

    def _append(self, chunk):
        if not chunk:
            return
        flt = self.filter_var.get().strip()
        self.text.config(state="normal")
        for line in chunk.splitlines():
            if flt and flt.lower() not in line.lower():
                continue
            tag = self._tag_for(line)
            self.text.insert("end", line + "\n", tag or ())
        # 限制缓冲区
        total = int(self.text.index("end-1c").split(".")[0])
        if total > self.tail_lines * 3:
            self.text.delete("1.0", f"{total - self.tail_lines * 2}.0")
        self.text.config(state="disabled")
        if self.autoscroll.get():
            self.text.see("end")

    def _clear_view(self):
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.config(state="disabled")

    def _reload_all(self):
        self._clear_view()
        self._pos = 0
        if not self.filepath or not os.path.exists(self.filepath):
            self.text.config(state="normal")
            self.text.insert("end", f"[尚未生成日志文件]\n{self.filepath}\n\n", "hint")
            if self.hint:
                self.text.insert("end", self.hint + "\n", "hint")
            self.text.insert("end", "\n窗口将持续监听，日志一旦产生会自动显示。\n", "hint")
            self.text.config(state="disabled")
            self.status.config(text="等待日志文件…")
            return
        try:
            size = os.path.getsize(self.filepath)
            with open(self.filepath, "rb") as f:
                # 只取尾部，避免超大文件卡顿
                back = min(size, 512 * 1024)
                f.seek(size - back)
                raw = f.read()
                self._pos = size
            content = raw.decode("utf-8", errors="replace").lstrip("\ufeff")
            lines = content.splitlines()
            if len(lines) > self.tail_lines:
                lines = lines[-self.tail_lines:]
            self._append("\n".join(lines))
            self.status.config(text=f"{size:,} 字节 · 实时刷新 {self.refresh_ms}ms")
        except Exception as e:
            self.status.config(text=f"读取失败: {e}")

    def _tick(self):
        if not self._alive or not self.winfo_exists():
            return
        try:
            if self.filepath and os.path.exists(self.filepath):
                size = os.path.getsize(self.filepath)
                if size < self._pos:          # 文件被重建/截断
                    self._reload_all()
                elif size > self._pos:
                    with open(self.filepath, "rb") as f:
                        f.seek(self._pos)
                        raw = f.read()
                        self._pos = f.tell()
                    self._append(raw.decode("utf-8", errors="replace")
                                 .lstrip("\ufeff"))
                    self.status.config(
                        text=f"{size:,} 字节 · {datetime.now():%H:%M:%S} 已更新")
                elif self._pos == 0:
                    self._reload_all()
        except Exception as e:
            self.status.config(text=f"刷新异常: {e}")
        self.after(self.refresh_ms, self._tick)

    def _open_external(self):
        if self.filepath and os.path.exists(self.filepath):
            os.startfile(self.filepath)

    def _close(self):
        self._alive = False
        self.destroy()


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------

class DesktopAIPet:
    def __init__(self):
        self.cfg = load_config()
        self.ap = self.cfg.get("desktop_aipet", {})
        self.menus = self.cfg.get("menus", [])
        # 桌宠昵称：配置 desktop_aipet.nickname，托盘/气泡显示「桌面AI助理-昵称」
        self.nickname = (self.ap.get("nickname") or "").strip()
        # 助理对用户的称呼：配置 desktop_aipet.boss，默认「老板」
        self.boss = (self.ap.get("boss") or "老板").strip() or "老板"
        # 桌宠随机台词：文案全部来自配置 "greetings"（仅文案，不含称呼）；
        # 称呼「{boss}，」由本代码在展示时统一拼接，不写进配置。
        raw_greetings = self.cfg.get("greetings")
        loaded = [str(g).strip() for g in raw_greetings
                  if isinstance(g, str) and str(g).strip()] \
            if isinstance(raw_greetings, list) else []
        self.greetings = loaded or list(_DEFAULT_GREETINGS)

        self.pet_visible = self.ap.get("pet_visible", True)
        self.scale = self.ap.get("pet_scale", 1.0)
        self.check_interval = int(self.ap.get("check_interval_s", 30))

        # 久坐提醒配置：来自 monitors 中 type=health_reminder 的条目（无 endpoint，
        # 不显示健康圆圈）。默认 7:00~22:00 每 60 分钟提醒一次，9:00 整段跳过。
        self.health_reminder = next(
            (m for m in self.menus if m.get("type") == "health_reminder"), {})
        _r_start = int(self.health_reminder.get("start_hour", 7))
        _r_end = int(self.health_reminder.get("end_hour", 22))
        # 间隔优先用 interval_minutes（分钟），兼容旧字段 interval_hour（小时）
        _iv_min = self.health_reminder.get("interval_minutes")
        if _iv_min is None:
            _iv_min = int(self.health_reminder.get("interval_hour", 1)) * 60
        _r_iv = max(1, int(_iv_min))
        _r_skip = set(int(h) for h in self.health_reminder.get("skip_hours", [9])
                      if isinstance(h, (int, float)))
        # 预计算所有触发时刻（当天分钟数）：从 start_hour:00 起按间隔取点，
        # 落在 [start,end] 且不在跳过小时内的时刻。间隔不再锚定整点。
        self._reminder_slots = set()
        _m = _r_start * 60
        _end_min = _r_end * 60
        while _m <= _end_min:
            if (_m // 60) not in _r_skip:
                self._reminder_slots.add(_m)
            _m += _r_iv
        self._reminder_last_key = None
        self._reminder_poll_ms = 20000

        self.pet_frames = {}
        self.pet_tk_frames = {}
        self.pet_theme = "robot"
        self.current_state = "normal"
        self.current_frame_idx = 0
        self.anim_speed_ms = 800

        self.tray_icons = {}
        self.tray = None

        self.monitor_states = {}
        self.last_notify_time = {}

        self.root = None
        self.canvas = None
        self.pet_image_id = None
        self.bubble = None
        self._drag = {"x": 0, "y": 0, "moved": False}
        self._running = False
        self._check_id = None
        self._anim_id = None
        self._fx_running = False
        self._blinking = False
        self._check_feedback = False
        self._last_click_t = 0.0
        self._ui_queue = queue.Queue()
        self._open_windows = {}

        # 更新检测：内置版本、待升级信息、GitHub 仓库（config 可覆盖）、自动检查间隔
        self._app_version = get_app_version()
        self._pending_update = None  # {"version","tag","url","name"}
        self.update_repo = get_update_repo(self.cfg)
        self.update_check_hours = float(self.ap.get("update_check_hours", 6))
        self._update_timer = None
        # 开机后延迟首次健康检测（秒），避免 Nanobot 等外部服务尚未启动即误报异常
        self.health_startup_delay_s = float(self.ap.get("health_startup_delay_s", 60))
        # 开机自启：与安装器（WiX 写入同一 Run 键）共用状态，天然同步
        self._startup_reg_name = "DesktopAIPet"

    # ------------------------------------------------------------------
    # UI thread marshalling —— 关键：托盘回调运行在托盘线程，
    # 所有 tkinter 操作必须回到主线程执行，否则静默崩溃。
    # ------------------------------------------------------------------

    def post_ui(self, fn):
        self._ui_queue.put(fn)

    def _pump_ui(self):
        if not self._running:
            return
        while True:
            try:
                fn = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                log.exception("UI 任务执行失败")
                self.notify("❌ 操作失败", "详情见 logs/aipet.log", "error")
        if self.root:
            self.root.after(50, self._pump_ui)

    def notify(self, title, message="", level="info"):
        log.info("[通知] %s | %s", title, message)
        if self.bubble:
            self.post_ui(lambda: self.bubble.show(title, message, level))

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def setup(self):
        self.pet_theme = self.ap.get("pet_theme") or "robot"
        theme_dir = PETS_DIR / self.pet_theme
        theme_dir.mkdir(parents=True, exist_ok=True)
        self.pet_frames = generate_pet_frames(self.scale, self.pet_theme)
        for state, frames in self.pet_frames.items():
            for i, img in enumerate(frames):
                try:
                    img.save(theme_dir / f"{state}_{i}.png")
                except Exception:
                    pass
        self.tray_icons = generate_tray_icons(self.pet_theme)

    # ------------------------------------------------------------------
    # Pet window
    # ------------------------------------------------------------------

    def _hide_from_taskbar(self, win=None):
        """Windows：将指定窗口（默认桌宠主窗口）设为工具窗口，避免在任务栏显示按钮。

        两个关键点（经实测验证）：
        1. winfo_id() 返回的是 Tk 的子窗口句柄，任务栏按钮由**顶层框架窗口**控制，
           必须用 GetAncestor(hwnd, GA_ROOT) 取顶层句柄再改扩展样式。
        2. -transparentcolor 会给窗口加 WS_EX_LAYERED 并清掉 overrideredirect 自带的
           WS_EX_TOOLWINDOW，因此必须在设置透明度**之后**再调用本方法。
        """
        if sys.platform != "win32":
            return
        target = win if win is not None else self.root
        if not target or not target.winfo_exists():
            return
        try:
            import ctypes
            GWL_EXSTYLE = -20
            GA_ROOT = 2
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_APPWINDOW = 0x00040000
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            SWP_FRAMECHANGED = 0x0020
            u32 = ctypes.windll.user32
            target.update_idletasks()
            hwnd = u32.GetAncestor(target.winfo_id(), GA_ROOT)
            if not hwnd:
                return
            exstyle = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            exstyle |= WS_EX_TOOLWINDOW
            exstyle &= ~WS_EX_APPWINDOW
            u32.SetWindowLongW(hwnd, GWL_EXSTYLE, exstyle)
            u32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
        except Exception:
            log.exception("隐藏任务栏窗口失败")

    def create_pet_window(self):
        self.root = tk.Tk()
        self.root.title("Desktop AIPet")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT_COLOR)

        size = int(72 * self.scale)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = self.ap.get("pet_x")
        y = self.ap.get("pet_y")
        # 位置缺失或超出屏幕时，重置到右下角
        if x is None or y is None or x + size > sw or y + size > sh or x < 0 or y < 0:
            x = sw - size - 20
            y = sh - size - 60

        self.root.geometry(f"{size}x{size}+{x}+{y}")
        self.root.update_idletasks()
        self.root.attributes("-transparentcolor", TRANSPARENT_COLOR)
        self._hide_from_taskbar()

        self.canvas = tk.Canvas(self.root, width=size, height=size,
                                bg=TRANSPARENT_COLOR, highlightthickness=0,
                                borderwidth=0)
        self.canvas.pack()

        for state, frames in self.pet_frames.items():
            self.pet_tk_frames[state] = [ImageTk.PhotoImage(f) for f in frames]

        self.pet_image_id = self.canvas.create_image(
            size // 2, size // 2, image=self.pet_tk_frames["normal"][0])

        self.canvas.bind("<Button-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_drag_end)
        self.canvas.bind("<Button-3>", self._on_pet_right_click)
        self.canvas.bind("<Double-Button-1>", self._on_pet_double_click)
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

    def _on_drag_start(self, event):
        self._drag.update({"x": event.x, "y": event.y, "moved": False})

    def _on_drag_move(self, event):
        self._drag["moved"] = True
        x = self.root.winfo_x() + event.x - self._drag["x"]
        y = self.root.winfo_y() + event.y - self._drag["y"]
        # 限制在屏幕边界内，避免拖出屏幕后无法找回
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        size = self.root.winfo_width()
        x = max(0, min(x, sw - size))
        y = max(0, min(y, sh - size))
        self.root.geometry(f"+{x}+{y}")

    def _on_drag_end(self, event):
        if self._drag["moved"]:
            self._save_config()
            return
        # 单击（未发生拖动）：机器人眨眼；用 0.3s 去抖避免与双击冲突
        now = time.time()
        if now - self._last_click_t < 0.3:
            return
        self._last_click_t = now
        self.play_blink()

    def _on_pet_double_click(self, event):
        self._last_click_t = time.time()
        # 双击：播放开心跳跃 + 跑动 + 弯眼互动动画，并随机展示一句台词
        self.play_double_click_anim()
        if self.bubble:
            self.bubble.show(self.assistant_display_name(),
                             self._pick_greeting(),
                             "info", duration=4000)

    def _on_pet_right_click(self, event):
        # 右键悬浮机器人：弹出菜单，提供「隐藏助理」入口（替代原眨眼）
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="🙈 隐藏助理", command=self._hide_pet_from_menu)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _hide_pet_from_menu(self):
        self.hide_pet()
        self._refresh_menu()  # 同步托盘菜单标签（召唤助理 ↔ 隐藏助理）

    # ------------------------------------------------------------------
    # Animation & state
    # ------------------------------------------------------------------

    def start_animation(self):
        self._animate()

    def _animate(self):
        if not self._running or not self.root or not self.canvas:
            return
        if self._fx_running or self._blinking:
            # 双击特效 / 眨眼进行中：暂停呼吸动画，结束后自动恢复
            self._anim_id = self.root.after(self.anim_speed_ms, self._animate)
            return
        frames = self.pet_tk_frames.get(self.current_state) \
            or self.pet_tk_frames.get("normal", [])
        if frames:
            self.current_frame_idx = (self.current_frame_idx + 1) % len(frames)
            self.canvas.itemconfig(self.pet_image_id,
                                   image=frames[self.current_frame_idx])
        self._anim_id = self.root.after(self.anim_speed_ms, self._animate)

    def play_double_click_anim(self):
        """双击桌宠：开心跳跃 + 跑动横向摆动 + 弯眼互动的庆祝动画。"""
        if not self.root or not self.canvas:
            return
        if self._fx_running:
            return  # 特效进行中，忽略重复触发
        self._fx_running = True
        happy = self.pet_tk_frames.get("happy") or self.pet_tk_frames["normal"]
        base_frames = self.pet_tk_frames.get(self.current_state,
                                             self.pet_tk_frames["normal"])
        base_x, base_y = self.root.winfo_x(), self.root.winfo_y()
        n = 28
        step_ms = 40

        def step(i):
            if i > n:
                # 复位：恢复位置与静止帧
                self._fx_running = False
                self.canvas.itemconfig(self.pet_image_id, image=base_frames[0])
                self.root.geometry(f"+{base_x}+{base_y}")
                return
            t = i / n
            hop = abs(math.sin(t * math.pi * 2)) ** 0.8 * 42   # 两次弹跳
            wig = math.sin(t * math.pi * 6) * 9                 # 跑动横向摆动
            self.root.geometry(f"+{base_x + int(wig)}+{base_y - int(hop)}")
            self.canvas.itemconfig(self.pet_image_id,
                                   image=happy[i % len(happy)])
            self.root.after(step_ms, step, i + 1)

        step(0)

    def play_blink(self):
        """单击桌宠（左/右键）：机器人眨眼互动（不弹健康提示）。"""
        if not self.root or not self.canvas:
            return
        if self._fx_running or self._blinking:
            return  # 特效/眨眼进行中，忽略重复触发
        self._blinking = True
        blink = self.pet_tk_frames.get("blink") or self.pet_tk_frames["normal"]
        base = self.pet_tk_frames.get(self.current_state,
                                      self.pet_tk_frames["normal"])[0]
        # 闭眼（约 150ms）后恢复当前静止帧
        self.canvas.itemconfig(self.pet_image_id, image=blink[0])
        self.root.after(150, lambda: (
            self.canvas.itemconfig(self.pet_image_id, image=base),
            setattr(self, "_blinking", False),
        ))

    def update_pet_state(self, state):
        if state == self.current_state:
            return
        self.current_state = state
        self.current_frame_idx = 0
        frames = self.pet_tk_frames.get(state) or self.pet_tk_frames.get("normal", [])
        if frames and self.pet_image_id and self.canvas:
            self.canvas.itemconfig(self.pet_image_id, image=frames[0])

    def hide_pet(self):
        if self.root:
            self.root.withdraw()
            self.pet_visible = False
            self._save_config()

    def show_pet(self):
        if self.root:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self._hide_from_taskbar()
            self.pet_visible = True
            self._save_config()

    def _pick_greeting(self):
        """从 greetings 随机挑一句纯文案，并拼接称呼前缀「{boss}，」。"""
        text = random.choice(self.greetings)
        return f"{self.boss}，{text}"

    def toggle_pet(self):
        if self.root and self.root.state() == "normal":
            self.hide_pet()
        else:
            self.show_pet()
            # 召唤助理点击 → 显示桌宠时，从 config.json 的 "greetings"
            # 随机选一句台词，在桌宠对话框（气泡）中展示。
            if self.root and self.bubble:
                self.bubble.show(self.assistant_display_name(),
                                 self._pick_greeting(),
                                 "info", duration=4000)
        self._refresh_menu()  # 更新菜单标签（召唤助理 ↔ 隐藏助理）

    def _rebuild_pet_tk_frames(self):
        """切换形象后，用新生成的 PIL 帧重建 Tk 帧并复位当前显示。"""
        try:
            if not hasattr(self, "pet_tk_frames"):
                self.pet_tk_frames = {}
            for state, frames in self.pet_frames.items():
                self.pet_tk_frames[state] = [ImageTk.PhotoImage(f) for f in frames]
            self.current_state = "normal"
            self.current_frame_idx = 0
            if self.pet_image_id is not None and self.canvas:
                self.canvas.itemconfig(self.pet_image_id,
                                       image=self.pet_tk_frames["normal"][0])
        except Exception:
            log.exception("重建桌宠帧失败")

    def switch_character(self):
        """菜单「切换助理」：循环切换到下一个桌宠形象并即时持久化。"""
        if not (self.root and self.canvas):
            return
        try:
            idx = CHARACTER_ORDER.index(self.pet_theme)
        except ValueError:
            idx = 0
        nxt = CHARACTER_ORDER[(idx + 1) % len(CHARACTER_ORDER)]
        self.pet_theme = nxt
        self.ap["pet_theme"] = nxt
        self._save_config()
        self.pet_frames = generate_pet_frames(self.scale, nxt)
        self._rebuild_pet_tk_frames()
        try:
            self.tray_icons = generate_tray_icons(nxt)
            if self.tray:
                self.update_tray_icon(self.current_state, True)
        except Exception:
            log.exception("切换助理：托盘图标更新失败")
        info = CHARACTER_INFO.get(nxt, {"name": nxt, "emoji": ""})
        self.notify("🎭 切换助理",
                    "已切换为 {} {}".format(info["name"], info["emoji"]), "ok")
        self._refresh_menu()
        log.info("切换桌宠形象为 %s", nxt)

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------

    def _find_action_by_type(self, atype):
        for m in self.menus:
            for a in m.get("actions", []):
                if self._action_type(a) == atype:
                    return a
        return None

    @staticmethod
    def _action_type(action):
        t = action.get("type")
        if t:
            return t
        # 兼容旧配置
        for key in ("url", "file", "cmd"):
            if action.get(key):
                return key
        return "noop"

    def make_handler(self, action, monitor):
        """生成托盘/桌宠菜单回调。所有执行统一编组到 UI 线程 + 全量异常捕获。"""
        def handler(*_args, **_kwargs):
            self.post_ui(lambda: self._execute_action(action, monitor))
        handler.__name__ = "act_" + str(action.get("label", "?"))
        return handler

    def _execute_action(self, action, monitor):
        label = action.get("label", "")
        atype = self._action_type(action)
        log.info("执行动作: %s (type=%s)", label, atype)
        try:
            fn = getattr(self, "_do_" + atype, None)
            if fn is None:
                self.notify("⚠️ 未知动作类型", f"{label}: {atype}", "warn")
                return
            fn(action, monitor)
        except Exception as e:
            log.exception("动作执行失败: %s", label)
            self.notify(f"❌ {label} 执行失败", str(e)[:180], "error")

    # -- action implementations --------------------------------------

    def _do_noop(self, action, monitor):
        self.notify("⚠️ 动作未配置", action.get("label", ""), "warn")

    def _do_url(self, action, monitor):
        url = action.get("url", "")
        os.startfile(url)
        self.notify(f"🌐 {action.get('label','')}", url, "ok")

    def _do_file(self, action, monitor):
        fp = expand(action.get("path") or action.get("file"))
        if not os.path.exists(fp):
            self.notify("⚠️ 文件不存在", fp, "warn")
            return
        os.startfile(fp)

    def _do_cmd(self, action, monitor):
        cmd = action.get("command") or action.get("cmd")
        visible = action.get("window", "hidden") == "visible"
        launch(["cmd.exe", "/c", cmd] if not action.get("keep_open")
               else ["cmd.exe", "/k", cmd], visible=visible)
        self.notify(f"✅ {action.get('label','')}", "命令已执行", "ok")

    def _do_powershell(self, action, monitor):
        cmd = action.get("command", "")
        visible = action.get("window", "visible") == "visible"
        prelude = ("[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
                   "$OutputEncoding=[System.Text.UTF8Encoding]::new(); "
                   "chcp 65001 > $null; ")
        full = prelude + cmd
        launch(powershell_args(full, no_exit=action.get("keep_open", True)),
               visible=visible)
        self.notify(f"📱 {action.get('label','')}",
                    "已在独立 PowerShell 窗口执行" if visible else "已后台执行", "ok")

    def _resolve_log_path(self, action, monitor):
        return expand(action.get("log_path") or monitor.get("log_path") or "")

    def _launch_script(self, path, step, monitor):
        """执行单个脚本步骤。step 支持 window / tee_log / wait / timeout。"""
        path = expand(path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"脚本不存在: {path}")
        visible = step.get("window", "visible") == "visible"
        log_file = self._resolve_log_path(step, monitor)

        if step.get("tee_log") and log_file:
            ps = build_tee_powershell(path, log_file,
                                      append=step.get("append_log", False),
                                      extra_env=step.get("env"))
            args = powershell_args(ps, no_exit=step.get("keep_open", True))
        else:
            args = ["cmd.exe", "/k" if (visible and step.get("keep_open"))
                    else "/c", path]

        return launch(args, visible=visible,
                      cwd=os.path.dirname(path) or None,
                      wait=bool(step.get("wait")),
                      timeout=step.get("timeout_s"))

    def _do_script(self, action, monitor):
        self._launch_script(action.get("path"), action, monitor)
        self.notify(f"▶ {action.get('label','')}",
                    os.path.basename(expand(action.get("path"))), "ok")

    def _do_script_seq(self, action, monitor):
        steps = action.get("steps") or []
        if not steps:
            raise ValueError("script_seq 缺少 steps 配置")
        label = action.get("label", "")
        delay = float(action.get("delay_s", 0))

        def worker():
            try:
                for i, step in enumerate(steps):
                    self._launch_script(step.get("path"), step, monitor)
                    if i < len(steps) - 1 and delay > 0:
                        time.sleep(delay)
                self.notify(f"🔄 {label}", "已完成停止 → 开始启动", "ok")
            except Exception as e:
                log.exception("序列执行失败: %s", label)
                self.notify(f"❌ {label} 失败", str(e)[:180], "error")

        threading.Thread(target=worker, daemon=True,
                         name="script-seq").start()
        self.notify(f"🔄 {label}", "正在执行…", "info")

    def _do_open_workspace(self, action, monitor):
        ws = expand(action.get("path") or monitor.get("workspace") or "")
        if not ws:
            cfg_file = expand(action.get("config_path")
                              or monitor.get("config_path") or "")
            key = action.get("config_key") or monitor.get(
                "workspace_key", "agents.defaults.workspace")
            if cfg_file and os.path.exists(cfg_file):
                with open(cfg_file, "r", encoding="utf-8-sig") as f:
                    ws = expand(deep_get(json.load(f), key, "") or "")
        if not ws:
            self.notify("⚠️ 未找到工作空间配置",
                        "请检查 config_path / config_key", "warn")
            return
        if not os.path.isdir(ws):
            self.notify("⚠️ 工作空间目录不存在", ws, "warn")
            return
        os.startfile(ws)
        self.notify("📁 跳转工作空间", ws, "ok")

    def _singleton_window(self, key, factory):
        """同一类弹窗只保留一个实例。"""
        win = self._open_windows.get(key)
        if win is not None and win.winfo_exists():
            win.deiconify()
            win.lift()
            win.focus_force()
            return win
        win = factory()
        self._open_windows[key] = win
        return win

    def _do_popup_file(self, action, monitor):
        fp = expand(action.get("path") or monitor.get("config_path") or "")
        title = action.get("title") or action.get("label") or "查看文件"
        self._singleton_window(
            "cfg:" + fp,
            lambda: ConfigViewer(self.root, title, fp,
                                 pretty_json=action.get("pretty_json", True)))

    def _do_popup_log(self, action, monitor):
        fp = self._resolve_log_path(action, monitor)
        title = action.get("title") or action.get("label") or "运行日志"
        hint = action.get("hint") or (
            "提示：日志由「启动 / 重启 Gateway」菜单托管启动时自动捕获。\n"
            "若 Gateway 是在助理之外手动启动的，则不会产生该文件。")
        self._singleton_window(
            "log:" + fp,
            lambda: LogViewer(self.root, title, fp,
                              refresh_ms=action.get("refresh_ms", 1000),
                              tail_lines=action.get("tail_lines", 800),
                              hint=hint))

    # ------------------------------------------------------------------
    # Menus
    # ------------------------------------------------------------------

    def _overall_summary(self):
        # 仅统计带 endpoint 的监控项；无 endpoint 的纯菜单组（如 AI 助理）不参与健康聚合

        enabled = [m for m in self.menus
                   if m.get("enabled", True) and m.get("endpoint")]
        if not enabled:
            return "无启用的监控项"
        bad = [m["name"] for m in enabled
               if not self.monitor_states.get(m["name"], (True, ""))[0]]
        if not bad:
            return f"全部正常 ({len(enabled)})"
        return "异常: " + "、".join(bad)

    def _monitor_status(self, name):
        ok, detail = self.monitor_states.get(name, (True, "等待检测"))
        return ("normal" if ok else "error"), detail

    def assistant_display_name(self):
        """托盘/气泡中显示的名称：桌面AI助理[-昵称]。"""
        base = "桌面AI助理"
        return f"{base}-{self.nickname}" if self.nickname else base

    def _build_tray_menu(self):
        # 顶层：聚合健康状态圆圈（🟢正常 / 🟡告警 / 🔴异常），仅作状态指示
        items = [
            pystray.MenuItem(
                lambda i: "{} {}".format(
                    STATUS_EMOJI.get(self.current_state, "🟢"),
                    self.assistant_display_name()),
                lambda *a: None, enabled=True),
            pystray.Menu.SEPARATOR,
        ]

        for m in self.menus:
            # enabled=false 的菜单项完全不展示（也不参与健康检测），
            # 方便非技术用户把不需要的技术类菜单整体关闭
            if not m.get("enabled", True):
                continue

            name = m.get("name", "")
            actions = m.get("actions", [])
            has_endpoint = bool(m.get("endpoint"))

            if has_endpoint:
                st = self._monitor_status(name)[0]
                status = STATUS_EMOJI[st]
                label_prefix = "{} ".format(status)
            else:
                # 无 endpoint 的纯菜单组（如 AI 助理）：不显示健康圆圈；
                # 若配置了 icon（如 🧠），作为分组图标前缀展示
                label_prefix = "{} ".format(m["icon"]) if m.get("icon") else ""

            if not actions:
                # 仅有健康监控、无快捷动作：直接展示状态圆圈+名称
                if label_prefix:
                    items.append(pystray.MenuItem(
                        "{}{}".format(label_prefix, name), None, enabled=False))
                else:
                    items.append(pystray.MenuItem(name, None, enabled=False))
                continue

            # 有动作：级联子菜单；级联标签用「前缀 名称」统一左对齐
            sub_items = []
            is_reminder = (m.get("type") == "health_reminder")
            rm_on = m.get("reminder_enabled", True)
            for act in actions:
                atype = self._action_type(act)
                label = act.get("label", "")
                icon = act.get("icon", "▶")
                if is_reminder:
                    if atype == "reminder_enable":
                        if rm_on:
                            label = "{} {} [✓]".format(icon, label)
                        else:
                            label = "{} {}".format(icon, label)
                    elif atype == "reminder_disable":
                        if not rm_on:
                            label = "{} {} [✓]".format(icon, label)
                        else:
                            label = "{} {}".format(icon, label)
                    else:
                        label = "{} {}".format(icon, label)
                else:
                    label = "{} {}".format(icon, label)
                sub_items.append(pystray.MenuItem(label, self.make_handler(act, m)))
            items.append(pystray.MenuItem(
                "{}{}".format(label_prefix, name),
                pystray.Menu(*sub_items)))

        # 将开机自启的勾选状态写入标签，避免 Windows 菜单 gutter 导致左侧空白参差
        startup_on = self._is_startup_enabled()
        startup_label = "⚙️ 开机自启 [{}]".format("✓" if startup_on else " ")

        more_items = [
            pystray.MenuItem("🩺 健康检测",
                             lambda *a: self.post_ui(self.force_check)),
            pystray.MenuItem(startup_label,
                             lambda *a: self.post_ui(self._toggle_startup)),
            pystray.MenuItem("🛠️ 助理配置",
                             lambda *a: self.post_ui(
                                 lambda: self._singleton_window(
                                     "cfg:self",
                                     lambda: ConfigViewer(
                                         self.root, "助理配置",
                                         str(CONFIG_PATH), editable=False,
                                         on_save=self._reload_config)))),
            pystray.MenuItem("🌀 重载配置",
                             lambda *a: self.post_ui(self._reload_config)),
        ]
        # 更新：有可用新版本时额外显示升级项
        if getattr(self, "_pending_update", None):
            more_items.append(
                pystray.MenuItem(
                    "⬆️ 升级到 v{}".format(self._pending_update["version"]),
                    lambda *a: self.post_ui(self.do_upgrade)))
        else:
            more_items.append(
                pystray.MenuItem("📡 检查更新",
                                 lambda *a: self.post_ui(
                                     lambda: self.check_update(notify=True))))
        more_menu = pystray.Menu(*more_items)

        items += [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda _: "🙈 隐藏助理" if self.pet_visible else "✨ 召唤助理",
                             lambda *a: self.post_ui(self.toggle_pet)),
            pystray.MenuItem(
                "🎭 切换助理",
                lambda *a: self.post_ui(self.switch_character)),
            pystray.MenuItem("📂 更多", more_menu),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🔁 重启", lambda *a: self.post_ui(self.restart_app)),
            pystray.MenuItem("❌ 退出", lambda *a: self.post_ui(self._on_exit)),
        ]
        return pystray.Menu(*items)

    # ------------------------------------------------------------------
    # Startup entry
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Startup entry —— 注册表 Run 键（与 MSI 安装器的「开机自启」选项共用同一状态）
    # ------------------------------------------------------------------
    _STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def _startup_command(self):
        """开机启动命令：冻结态指向 exe 自身，开发态用 pythonw 跑脚本。"""
        if getattr(sys, "frozen", False):
            return '"{}"'.format(os.path.normpath(sys.executable))
        return '"{}" "{}"'.format(
            os.path.normpath(sys.executable),
            os.path.normpath(BASE_DIR / "aipet.py"))

    def _is_startup_enabled(self):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._STARTUP_REG_PATH) as k:
                winreg.QueryValueEx(k, self._startup_reg_name)
            return True
        except OSError:
            return False

    def _set_startup(self, enabled):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._STARTUP_REG_PATH,
                            0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, self._startup_reg_name, 0,
                                  winreg.REG_SZ, self._startup_command())
            else:
                try:
                    winreg.DeleteValue(k, self._startup_reg_name)
                except OSError:
                    pass

    def _toggle_startup(self):
        try:
            enabled = not self._is_startup_enabled()
            self._set_startup(enabled)
            self.notify("⚙️ 开机自启",
                        "已开启" if enabled else "已关闭",
                        "ok" if enabled else "info")
            # 刷新菜单以更新开机自启标签中的 [✓]/[ ]
            self._refresh_menu()
        except Exception as e:
            log.exception("切换开机自启失败")
            self.notify("❌ 开机自启设置失败", str(e)[:180], "error")

    # ------------------------------------------------------------------
    # Tray
    # ------------------------------------------------------------------

    def create_tray_icon(self):
        icon = self.tray_icons.get("normal") or next(iter(self.tray_icons.values()))
        self.tray = self._TrayIcon("desktop-aipet", icon=icon,
                              title=self.assistant_display_name(),
                              menu=self._build_tray_menu(),
                              on_double_click=self._on_tray_double_click)

    def _on_tray_double_click(self, icon):
        # 双击托盘图标：召唤（显示）桌面悬浮助理，免去右键菜单操作
        self.post_ui(self._summon_pet)


    class _TrayIcon(_pystray_win32.Icon):
        """支持双击召唤的托盘图标。

        pystray 当前版本不支持 double_click，且左键单击会弹模态菜单吞掉双击消息。
        本子类在托盘线程内做：左键抬起后延时 300ms 再弹菜单；若 400ms 内再次出现
        左键抬起（双击）则取消菜单并触发 on_double_click 回调。
        """
        WM_SHOW_MENU = _pystray_win32_util.WM_USER + 20
        WM_LBUTTONDBLCLK = 0x0203
        _DBLCLICK_SEC = 0.4
        _MENU_DELAY = 0.3

        def __init__(self, *args, on_double_click=None, **kwargs):
            super().__init__(*args, **kwargs)
            self._on_double_click = on_double_click
            self._last_up = -1e9  # 初始化为极小值，避免首击被误判为双击
            self._menu_timer = None
            self._message_handlers[self.WM_SHOW_MENU] = self._do_show_menu

        def _do_show_menu(self, wparam, lparam):
            # 在托盘线程内执行默认菜单弹出（勿跨线程调用 TrackPopupMenuEx）
            super()._on_notify(0, _pystray_win32_util.WM_LBUTTONUP)

        def _on_notify(self, wparam, lparam):
            if lparam in (_pystray_win32_util.WM_LBUTTONUP, self.WM_LBUTTONDBLCLK):
                now = _time.time()
                if now - self._last_up < self._DBLCLICK_SEC:
                    # 双击：召唤助理，取消待弹菜单
                    self._last_up = 0.0
                    if self._menu_timer is not None:
                        self._menu_timer.cancel()
                        self._menu_timer = None
                    if self._on_double_click:
                        self._on_double_click(self)
                    return
                self._last_up = now
                # 先不立即弹菜单，观望是否双击
                if self._menu_timer is not None:
                    self._menu_timer.cancel()
                self._menu_timer = _threading.Timer(
                    self._MENU_DELAY, self._post_show_menu)
                self._menu_timer.daemon = True
                self._menu_timer.start()
                return
            super()._on_notify(wparam, lparam)

        def _post_show_menu(self):
            self._menu_timer = None
            try:
                _pystray_win32_util.win32.PostMessage(
                    self._hwnd, self.WM_SHOW_MENU, 0, 0)
            except Exception:
                pass

    def _summon_pet(self):
        if not (self.root and self.root.state() == "normal"):
            self.show_pet()
        if self.bubble:
            self.bubble.show(self.assistant_display_name(),
                             self._pick_greeting(), "info", duration=4000)
        self._refresh_menu()

    def update_tray_icon(self, state, changed):
        if not self.tray:
            return
        try:
            if state in self.tray_icons:
                self.tray.icon = self.tray_icons[state]
            self.tray.title = self.assistant_display_name()
        except Exception:
            log.exception("更新托盘图标失败")

    def _refresh_menu(self):
        """重建托盘菜单，使状态圆圈（🟢/🟡/🔴）与实际状态同步。"""
        if not self.tray:
            return
        try:
            self.tray.menu = self._build_tray_menu()
            self.tray.update_menu()
        except Exception:
            log.exception("刷新托盘菜单失败")

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def force_check(self):
        if self._check_id and self.root:
            try:
                self.root.after_cancel(self._check_id)
            except Exception:
                pass
        self._check_feedback = True
        self.notify("🩺 健康检测", "正在检测服务状态…", "info")
        self.run_health_check()

    def run_health_check(self):
        if not self._running:
            return
        # 仅探测带 endpoint 的监控项；无 endpoint 的纯菜单组（如 AI 助理）不参与探活

        enabled = [m for m in self.menus
                   if m.get("enabled", True) and m.get("endpoint")]
        if not enabled:
            self._schedule_next_check()
            return

        threading.Thread(target=self._probe_worker, args=(enabled,),
                         daemon=True, name="health-probe").start()

    def _probe_worker(self, enabled):
        results = [check_monitor(m) for m in enabled]
        self.post_ui(lambda: self._apply_results(results))

    def _apply_results(self, results):
        feedback = self._check_feedback
        self._check_feedback = False
        all_ok, any_ok = True, False
        monitor_changed = False
        for name, ok, detail in results:
            prev_ok, _ = self.monitor_states.get(name, (True, ""))
            if ok != prev_ok:
                monitor_changed = True
            self.monitor_states[name] = (ok, detail)
            if ok:
                any_ok = True
                log.info("[OK]   %s | %s", name, detail)
            else:
                all_ok = False
                log.warning("[FAIL] %s | %s", name, detail)
            if ok != prev_ok and not ok:
                now = datetime.now()
                if now - self.last_notify_time.get(name, datetime.min) > timedelta(minutes=5):
                    self.notify(f"⚠️ {name} 异常", detail, "error")
                    self.last_notify_time[name] = now

        new_state = "normal" if all_ok else ("warning" if any_ok else "error")
        changed = new_state != self.current_state
        self.update_pet_state(new_state)
        # 聚合状态、任一监控项状态、或强制检测发生变化 → 重建菜单同步状态圆圈
        if changed or monitor_changed or feedback:
            self._refresh_menu()
        self.update_tray_icon(new_state, changed)
        if feedback:
            level = {"normal": "ok", "warning": "warn",
                     "error": "error"}.get(new_state, "info")
            self.notify("✅ 检测完成", self._overall_summary(), level)
        self._schedule_next_check()

    def _schedule_next_check(self):
        if self._running and self.root:
            self._check_id = self.root.after(
                self.check_interval * 1000, self.run_health_check)

    # ------------------------------------------------------------------
    # 久坐提醒（Stand-up reminder）
    # ------------------------------------------------------------------

    def start_health_reminder(self):
        """启动久坐提醒轮询（主线程 root.after 递归调度）。"""
        if self._running and self.root:
            self.root.after(self._reminder_poll_ms, self._health_reminder_tick)

    def _health_reminder_tick(self):
        if not (self._running and self.root):
            return
        try:
            self._maybe_remind()
        except Exception:
            log.exception("久坐提醒检查失败")
        # 无论是否触发，都继续下一轮轮询
        self.root.after(self._reminder_poll_ms, self._health_reminder_tick)

    def _maybe_remind(self):
        cfg = self.health_reminder
        # enabled=false：菜单整体隐藏、不做提醒；
        # reminder_enabled=false：菜单存在但提醒本身关闭
        if not cfg.get("enabled", True) or not cfg.get("reminder_enabled", True):
            return
        now = time.localtime()
        # 仅当「当前时刻(当天分钟数)」落在预计算的提醒时刻集合内才触发
        now_min = now.tm_hour * 60 + now.tm_min
        if now_min not in self._reminder_slots:
            return
        # 同一时刻只提醒一次，避免轮询重复弹出
        key = (now.tm_year, now.tm_mon, now.tm_mday, now_min)
        if key == self._reminder_last_key:
            return
        self._reminder_last_key = key
        msg = (cfg.get("message") or "").strip()
        if not msg:
            return
        title = self.assistant_display_name()
        if self.bubble:
            self.bubble.show(title, msg, "warn", duration=8000)
        log.info("久坐提醒触发 | %02d:%02d | %s", now.tm_hour, now.tm_min, msg)

    def set_reminder_enabled(self, on):
        """开启/关闭久坐提醒（reminder_enabled），持久化到 config.json 并刷新托盘菜单状态。"""
        try:
            self.health_reminder["reminder_enabled"] = bool(on)
        except Exception:
            self.health_reminder = {"reminder_enabled": bool(on)}
        self._save_config()
        self._refresh_menu()
        self.notify("🪑 久坐提醒",
                    "已开启，将按配置时段提醒你起身活动。" if on
                    else "已关闭久坐提醒。",
                    "info")
        log.info("久坐提醒已%s", "开启" if on else "关闭")

    def test_reminder(self):
        """立即弹出一次久坐提醒（忽略时段与时间），用于验证提醒效果。"""
        cfg = self.health_reminder
        msg = (cfg.get("message") or "").strip()
        if not msg:
            msg = "该起身活动一下身体啦～"
        title = self.assistant_display_name()
        if self.bubble:
            self.bubble.show(title, msg, "warn", duration=8000)
        log.info("久坐提醒（测试）触发 | %s", msg)

    # -- 久坐提醒菜单动作（由 config.json 的 health_reminder.menu 驱动）--

    def _do_reminder_enable(self, action, monitor):
        """开启久坐提醒（菜单动作 type=reminder_enable）。"""
        self.set_reminder_enabled(True)

    def _do_reminder_disable(self, action, monitor):
        """关闭久坐提醒（菜单动作 type=reminder_disable）。"""
        self.set_reminder_enabled(False)

    def _do_reminder_test(self, action, monitor):
        """立即测试弹出久坐提醒（菜单动作 type=reminder_test）。"""
        self.test_reminder()

    # ------------------------------------------------------------------
    # Config persistence
    # ------------------------------------------------------------------

    def _reload_config(self):
        """从磁盘重新读取 config.json，刷新全部运行状态（菜单/昵称/宠物参数等）。"""
        try:
            new_cfg = load_config()
        except Exception:
            log.exception("重载配置失败：无法读取 config.json")
            self.notify("❌ 重载配置失败", "无法读取配置文件", "error")
            return

        self.cfg = new_cfg
        self.ap = self.cfg.get("desktop_aipet", {})
        self.menus = self.cfg.get("menus", [])

        # 更新昵称 / 称呼 / 台词
        self.nickname = (self.ap.get("nickname") or "").strip()
        self.boss = (self.ap.get("boss") or "老板").strip() or "老板"
        raw = self.cfg.get("greetings")
        loaded = [str(g).strip() for g in raw
                  if isinstance(g, str) and str(g).strip()] \
            if isinstance(raw, list) else []
        self.greetings = loaded or list(_DEFAULT_GREETINGS)

        # 更新宠物参数
        self.pet_visible = self.ap.get("pet_visible", True)
        self.scale = self.ap.get("pet_scale", 1.0)
        self.check_interval = int(self.ap.get("check_interval_s", 30))

        # 桌宠形象：若配置中 pet_theme 变化，则重新生成帧与托盘图标
        new_char = self.ap.get("pet_theme") or "robot"
        if new_char != self.pet_theme:
            self.pet_theme = new_char
            self.pet_frames = generate_pet_frames(self.scale, new_char)
            self._rebuild_pet_tk_frames()
            try:
                self.tray_icons = generate_tray_icons(new_char)
            except Exception:
                log.exception("重载：托盘图标更新失败")

        # 久坐提醒：重新从 menus 中定位
        self.health_reminder = next(
            (m for m in self.menus if m.get("type") == "health_reminder"), {})

        # 重置监控状态，下一轮健康探活将使用最新配置
        self.monitor_states.clear()

        # 重建托盘菜单
        self._refresh_menu()
        if self.tray:
            self.tray.title = self.assistant_display_name()

        self.notify("✅ 配置已重载", "", "ok")
        log.info("配置已重载 | menus=%d | interval=%ds",
                 len(self.menus), self.check_interval)

    def _save_config(self):
        try:
            if self.root and self.root.state() == "normal":
                self.ap["pet_x"] = self.root.winfo_x()
                self.ap["pet_y"] = self.root.winfo_y()
            self.ap["pet_visible"] = (
                self.root is not None and self.root.state() == "normal")
            self.cfg["desktop_aipet"] = self.ap
            # 以磁盘配置为基准、内存配置为覆盖做深合并：
            # 保留用户外部新增的嵌套键（如 desktop_aipet.nickname、
            # greetings 等），同时让运行时变更生效。
            try:
                on_disk = load_config()
            except Exception:
                on_disk = {}
            merged = _deep_merge(on_disk, self.cfg)
            # 兜底：确保已废弃的 pet_character 不会写回磁盘
            merged.get("desktop_aipet", {}).pop("pet_character", None)
            save_config(merged)
        except Exception:
            log.exception("保存配置失败")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _on_exit(self):
        log.info("正在退出 桌面AI助理...")
        self._running = False
        for attr in ("_check_id", "_anim_id"):
            aid = getattr(self, attr)
            if aid and self.root:
                try:
                    self.root.after_cancel(aid)
                except Exception:
                    pass
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        if self.root:
            try:
                self.root.destroy()
            except Exception:
                pass
        log.info("桌面AI助理 已退出")
        os._exit(0)

    # ------------------------------------------------------------------
    # 更新检测与升级（GitHub Releases 作为更新源）
    # ------------------------------------------------------------------
    def check_update(self, notify=True):
        """检查 GitHub 是否有更高版本；有则记录 _pending_update 并提醒。"""
        try:
            import httpx
            repo = self.update_repo
            if not repo or repo == DEFAULT_UPDATE_REPO:
                if notify:
                    self.notify("📡 更新", "未配置 GitHub 仓库，无法检查更新",
                                "info")
                return
            cur_v = self._app_version
            url = "https://api.github.com/repos/{}/releases/latest".format(repo)
            with httpx.Client(timeout=15, follow_redirects=True,
                              headers={"User-Agent": "desktop-aipet-updater"}) as c:
                r = c.get(url)
                if r.status_code == 404:
                    # GitHub 在仓库没有任何已发布 Release 时，对
                    # /releases/latest 返回 404，属正常情况而非错误。
                    self._pending_update = None
                    if notify:
                        self.notify("✅ 已是最新",
                                    "当前 v{}（仓库暂无发布版本）".format(
                                        version_to_str(cur_v)), "ok")
                    return
                if r.status_code != 200:
                    if notify:
                        self.notify("📡 更新",
                                    "检查失败 HTTP {}".format(r.status_code),
                                    "error")
                    return
                data = r.json()
            tag = data.get("tag_name", "")
            remote_v = parse_version(tag)
            if not any(remote_v):
                if notify:
                    self.notify("📡 更新", "未获取到版本信息", "error")
                return
            assets = data.get("assets", [])
            asset = select_update_asset(assets)
            if remote_v > cur_v:
                self._pending_update = {
                    "version": version_to_str(remote_v),
                    "tag": tag,
                    "url": asset.get("browser_download_url") if asset else None,
                    "name": asset.get("name") if asset else None,
                }
                self.notify(
                    "🎉 发现新版本 v{}".format(version_to_str(remote_v)),
                    "当前 v{}，点击「⬆️ 升级」可一键更新".format(
                        version_to_str(cur_v)),
                    "ok")
                self._refresh_menu()
            else:
                self._pending_update = None
                if notify:
                    self.notify("✅ 已是最新",
                                "当前 v{}".format(version_to_str(cur_v)), "ok")
        except Exception as e:
            log.exception("检查更新失败")
            if notify:
                self.notify("❌ 更新检查失败", str(e)[:180], "error")

    def do_upgrade(self):
        """下载最新 release 资产并覆盖本地代码（保留 config/pets/icons/logs）。"""
        info = getattr(self, "_pending_update", None)
        if not info or not info.get("url"):
            self.notify("⬆️ 升级", "暂无可用更新", "info")
            return
        try:
            import httpx
            self.notify("⬆️ 升级", "正在下载 v{} ...".format(info["version"]),
                        "info")
            tmp = tempfile.mkdtemp(prefix="aipet_upg_")
            zip_path = os.path.join(tmp, "update.zip")
            with httpx.Client(timeout=180, follow_redirects=True,
                              headers={"User-Agent": "desktop-aipet-updater"}) as c:
                with c.stream("GET", info["url"]) as resp:
                    resp.raise_for_status()
                    with open(zip_path, "wb") as f:
                        for chunk in resp.iter_bytes():
                            f.write(chunk)
            extract_dir = os.path.join(tmp, "extracted")
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(zip_path) as z:
                z.extractall(extract_dir)
            # 定位含可执行文件的真实根目录（release 资产可能包了一层文件夹）
            src_root = extract_dir
            for root, _dirs, files in os.walk(extract_dir):
                if "aipet.exe" in files or "aipet.py" in files:
                    src_root = root
                    break
            # 生成更新批处理：退出后覆盖【安装根目录】(EXE_DIR) 的代码，
            # 保留配置与运行时数据。冻结态 EXE_DIR = sys.executable 所在目录。
            bat = os.path.join(tmp, "update.bat")
            exe_path = sys.executable
            py_path = os.path.join(BASE_DIR, "aipet.py")
            lines = [
                "@echo off",
                "chcp 65001 >nul",
                "timeout /t 2 /nobreak >nul",
                'robocopy "{src}" "{dst}" /E /PURGE /XF config.json /XD pets icons logs'.format(
                    src=src_root, dst=str(EXE_DIR)),
                'if exist "{exe}" ('.format(exe=exe_path),
                '  start "" "{exe}"'.format(exe=exe_path),
                ") else (",
                '  start "" "{pyexe}" "{pyscript}"'.format(
                    pyexe=sys.executable, pyscript=py_path),
                ")",
                'rmdir /s /q "{tmp}"'.format(tmp=tmp),
            ]
            with open(bat, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            # 保存配置并退出，由 update.bat 完成覆盖与重启
            self._save_config()
            if self.tray:
                try:
                    self.tray.stop()
                except Exception:
                    pass
            if self.root:
                try:
                    self.root.destroy()
                except Exception:
                    pass
            subprocess.Popen(bat, creationflags=CREATE_NEW_CONSOLE, shell=True)
            os._exit(0)
        except Exception as e:
            log.exception("升级失败")
            self.notify("❌ 升级失败", str(e)[:180], "error")

    def _schedule_update_check(self):
        """后台线程静默检查更新，并按配置间隔循环。"""
        try:
            threading.Thread(target=self.check_update, kwargs={"notify": False},
                             daemon=True, name="update-check").start()
        except Exception:
            log.exception("启动更新检查线程失败")
        try:
            self._update_timer = self.root.after(
                int(self.update_check_hours * 3600 * 1000),
                self._schedule_update_check)
        except Exception:
            pass

    def _start_auto_update(self):
        """启动后延迟一段时间首次检查更新，再进入循环。"""
        if not getattr(self, "root", None):
            return
        self._update_timer = self.root.after(60000, self._schedule_update_check)

    def restart_app(self):
        """重启桌面宠物：先停掉当前托盘/窗口并保存状态，后台拉起新实例后退出。"""
        try:
            self._save_config()
            if self.tray:
                try:
                    self.tray.stop()
                except Exception:
                    pass
            if self.root:
                try:
                    self.root.destroy()
                except Exception:
                    pass
            # 冻结态直接拉起 exe 自身；开发态用 pythonw 跑脚本
            if getattr(sys, "frozen", False):
                args = []
            else:
                args = [str(BASE_DIR / "aipet.py")]
            subprocess.Popen([sys.executable, *args],
                             creationflags=CREATE_NO_WINDOW)
            log.info("重启：已启动新实例，准备退出当前实例")
        except Exception:
            log.exception("重启失败")
        os._exit(0)

    def run(self):
        self.setup()
        log.info("桌面AI助理 启动 | 大小: %dpx | 间隔: %ds | 监控: %d 项",
                 int(72 * self.scale), self.check_interval, len(self.menus))

        self.create_pet_window()
        if not self.pet_visible:
            self.root.withdraw()

        self.bubble = PetBubble(self)
        self.create_tray_icon()
        self._running = True

        self._pump_ui()
        # 开机后延迟首次健康检测，避免 Nanobot 等外部服务尚未启动好即误报异常
        self.root.after(int(self.health_startup_delay_s * 1000),
                        self.run_health_check)
        self.start_health_reminder()
        self.start_animation()
        self._start_auto_update()  # 启动后延迟检查 GitHub 更新并按间隔循环

        threading.Thread(target=self.tray.run, daemon=True,
                         name="tray").start()

        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self._on_exit()


# ---------------------------------------------------------------------------
# Single-instance guard (Windows named mutex)
# ---------------------------------------------------------------------------
def ensure_single_instance():
    """确保同时只有一个桌面AI助理实例在运行。

    借助系统级命名互斥量(Global\\DesktopAIPet_SingleInstance)检测已有实例:
    若发现已有实例,则尝试把它的桌宠窗口提到前台,并弹出"已在运行"提示框,
    避免用户误以为"点了没反应";随后退出当前进程,防止多开互相覆盖配置。
    非 Windows 平台直接放行。
    """
    if sys.platform != "win32":
        return
    ERROR_ALREADY_EXISTS = 183
    SW_RESTORE = 9
    MB_OK = 0
    handle = ctypes.windll.kernel32.CreateMutexW(
        None, True, "Global\\DesktopAIPet_SingleInstance")
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        hwnd = ctypes.windll.user32.FindWindowW(None, "Desktop AIPet")
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        ctypes.windll.user32.MessageBoxW(
            None, "桌面AI助理已在运行。", "提示", MB_OK)
        os._exit(0)
    # 句柄保持打开:进程存活期间持续占用,退出后由系统回收


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ensure_single_instance()
    try:
        DesktopAIPet().run()
    except Exception:
        log.exception("启动失败")
        raise
