#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Desktop AI Pet - 独立 Windows 安装程序（无需外部打包工具）。

使用 PyInstaller 构建为 desktop-aipet-setup.exe；将已冻结的应用（dist/aipet）
作为资源打包，安装时复制到用户指定目录，并可选创建桌面快捷方式、
开机自启注册表项及卸载入口。
"""
import os
import re
import sys
import shutil
import subprocess
import tempfile
import winreg
import json

import tkinter as tk
from tkinter import filedialog, messagebox

APP_NAME = "桌面AI助理"
SHORTCUT_NAME = "桌面AI助理.lnk"
STARTUP_VAL = "DesktopAIPet"
REG_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\DesktopAIPet"
REG_APP = r"Software\zhixinglvren\desktop-aipet"


def is_frozen():
    return getattr(sys, "frozen", False)


def app_src_dir():
    """已打包应用所在目录（冻结态：MEIPASS/app；开发态：dist/aipet）。"""
    if is_frozen():
        return os.path.join(sys._MEIPASS, "app")
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "dist", "aipet")


def res_dir():
    if is_frozen():
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def get_version():
    try:
        p = os.path.join(res_dir(), "version.txt")
        if os.path.exists(p):
            return (open(p, encoding="utf-8").read().strip() or "1.0.0")
    except Exception:
        pass
    return "1.0.0"


def default_install_dir():
    return os.path.join(os.path.expandvars("%LOCALAPPDATA%"),
                        "Programs", "desktop-aipet")


def detect_installed_dir():
    """探测已安装目录，用于安装/升级时自动填入上次路径。

    依次尝试：
      1) 卸载项 HKCU\\...\\Uninstall\\DesktopAIPet 的 InstallLocation
      2) 应用项 HKCU\\Software\\zhixinglvren\\desktop-aipet 的 InstallDir
      3) 开机自启 Run 键 DesktopAIPet（值为 "安装目录\\aipet.exe"）
    命中则返回 (path, is_upgrade)，否则 (None, False)。
    is_upgrade 指该目录确实存在 aipet.exe（即上次安装完整）。
    """
    def _read(key_path, value_name):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as k:
                try:
                    return winreg.QueryValueEx(k, value_name)[0]
                except FileNotFoundError:
                    return None
        except FileNotFoundError:
            return None

    candidates = []
    loc = _read(REG_UNINSTALL, "InstallLocation")
    if loc:
        candidates.append(loc)
    appdir = _read(REG_APP, "InstallDir")
    if appdir:
        candidates.append(appdir)
    run_val = _read(REG_RUN, STARTUP_VAL)
    if run_val:
        run_val = str(run_val).strip().strip('"')
        if run_val.lower().endswith("aipet.exe"):
            candidates.append(os.path.dirname(run_val))

    for c in candidates:
        if c and os.path.isdir(c):
            is_upgrade = os.path.exists(os.path.join(c, "aipet.exe"))
            return c, is_upgrade
    return None, False



def load_license_text():
    """读取并解析 LICENSE.rtf，返回纯文本（冻结态在 _MEIPASS，开发态在脚本目录）。"""
    p = os.path.join(res_dir(), "LICENSE.rtf")
    if not os.path.exists(p):
        return "（未找到许可协议文件 LICENSE.rtf）"
    try:
        with open(p, encoding="utf-8", errors="ignore") as fh:
            raw = fh.read()
    except Exception:
        return "（无法读取许可协议文件）"
    return rtf_to_text(raw)


def rtf_to_text(rtf):
    r"""极简 RTF -> 文本：丢弃字体表，\par 转换行，去除控制词与花括号分组。"""
    text = re.sub(r"\{\\*?\\fonttbl.*?\}\}", "", rtf, flags=re.S)
    text = re.sub(r"\{\\info.*?\}", "", text, flags=re.S)
    text = re.sub(r"\\par\b", "\n", text)
    text = re.sub(r"\\[a-zA-Z]+\d*\s?", "", text)
    text = text.replace("\\{", "{").replace("\\}", "}").replace("\\\\", "\\")
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"^\s*\w+;\s*$", "", text, flags=re.M)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return text.strip() + "\n"


def desktop_path():
    return os.path.join(os.path.expandvars("%USERPROFILE%"), "Desktop")


class Installer(tk.Tk):
    def __init__(self):
        super().__init__()
        detected, is_upgrade = detect_installed_dir()
        self.is_upgrade = is_upgrade
        self.detected_dir = detected
        default = detected if detected else default_install_dir()
        self.title(("桌面AI助理升级程序" if is_upgrade else "桌面AI助理安装程序")
                   + "  -  v" + get_version())
        self.resizable(False, False)
        self._set_window_icon()
        self.install_dir = tk.StringVar(value=default)
        self.opt_shortcut = tk.BooleanVar(value=True)
        self.opt_startup = tk.BooleanVar(value=False)
        self.opt_launch = tk.BooleanVar(value=True)
        self.opt_health_reminder = tk.BooleanVar(value=True)
        self.opt_ai_nanobot = tk.BooleanVar(value=True)
        self.opt_ai_claude = tk.BooleanVar(value=True)
        self.opt_ai_codex = tk.BooleanVar(value=True)
        self.opt_ai_opencode = tk.BooleanVar(value=True)
        # 升级模式：按已装 config.json 的 enabled 预置勾选，使界面反映当前配置
        self._preset_feature_flags(detected)
        self.busy = False
        self._build()
        self._center_window()

    def _set_window_icon(self):
        """设置窗口/任务栏图标为机器人图标。

        优先使用 app.ico；若 tkinter 无法加载（Pillow 生成的 ICO 在部分
        环境下只保留一帧，导致 Windows 标题栏/任务栏不显示），则回退到
        app.png 并用 iconphoto 设置。
        """
        ico = os.path.join(res_dir(), "app.ico")
        png = os.path.join(res_dir(), "app.png")
        if os.path.exists(ico):
            try:
                self.iconbitmap(ico)
                return
            except Exception:
                pass
        if os.path.exists(png):
            try:
                photo = tk.PhotoImage(file=png)
                self.iconphoto(True, photo)
            except Exception:
                pass

    def _center_window(self):
        """将窗口居中到 Windows 桌面（屏幕中央）。"""
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 1 or height <= 1:
            width = self.winfo_reqwidth()
            height = self.winfo_reqheight()
        x = (self.winfo_screenwidth() - width) // 2
        y = (self.winfo_screenheight() - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _preset_feature_flags(self, detected):
        """升级模式：读取已装 config.json 的 enabled 状态，预置功能勾选框，
        让安装界面如实反映当前配置；全新安装（detected 为空）不调用，保持默认全勾选。
        """
        if not detected:
            return
        cfg_path = os.path.join(detected, "config.json")
        if not os.path.exists(cfg_path):
            return
        try:
            with open(cfg_path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except Exception:
            return
        ai_names = {"Nanobot", "Claude Code", "Codex", "OpenCode"}
        for m in cfg.get("menus", []):
            name = m.get("name", "")
            enabled = bool(m.get("enabled", True))
            if m.get("type") == "health_reminder":
                self.opt_health_reminder.set(enabled)
            elif name in ai_names:
                if name == "Nanobot":
                    self.opt_ai_nanobot.set(enabled)
                elif name == "Claude Code":
                    self.opt_ai_claude.set(enabled)
                elif name == "Codex":
                    self.opt_ai_codex.set(enabled)
                elif name == "OpenCode":
                    self.opt_ai_opencode.set(enabled)

    def _build(self):
        f = tk.Frame(self, padx=24, pady=12)
        f.pack()
        title_text = ("升级 桌面AI助理" if self.is_upgrade else "安装 桌面AI助理")
        tk.Label(f, text=title_text,
                 font=("Microsoft YaHei UI", 14, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        # 许可协议：滚动只读文本框 + 同意勾选
        lic_frame = tk.LabelFrame(f, text="开源软件许可与免责声明", padx=8, pady=6)
        lic_frame.grid(row=1, column=0, columnspan=3, sticky="we", pady=(0, 8))
        lic_frame.columnconfigure(0, weight=1)
        lic_text = tk.Text(lic_frame, width=72, height=8, wrap="word",
                           state="disabled", bg="#f7f7f7",
                           font=("Microsoft YaHei UI", 9))
        lic_scroll = tk.Scrollbar(lic_frame, command=lic_text.yview)
        lic_text.configure(yscrollcommand=lic_scroll.set)
        lic_text.grid(row=0, column=0, sticky="nsew")
        lic_scroll.grid(row=0, column=1, sticky="ns")
        lic_text.configure(state="normal")
        lic_text.insert("1.0", load_license_text())
        lic_text.configure(state="disabled")

        self.opt_agree = tk.BooleanVar(value=False)
        tk.Checkbutton(f, text="我已阅读并同意上述许可协议",
                       variable=self.opt_agree).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(0, 8))

        tk.Label(f, text="安装位置：").grid(row=3, column=0, sticky="w")
        tk.Entry(f, textvariable=self.install_dir, width=58).grid(
            row=3, column=1, padx=6)
        tk.Button(f, text="浏览...", command=self._browse).grid(
            row=3, column=2)

        tk.Checkbutton(f, text="创建桌面快捷方式",
                       variable=self.opt_shortcut).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(8, 2))
        tk.Checkbutton(f, text="Windows 开机自动启动",
                       variable=self.opt_startup).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(2, 2))
        tk.Checkbutton(f, text="安装完成后启动桌面AI助理",
                       variable=self.opt_launch).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(2, 8))

        # 功能选项：勾选的功能安装后默认启用；未勾选的会在 config.json 中
        # 将对应菜单项 enabled 设为 false（运行时菜单隐藏）。
        # 升级模式同样展示，并按已装配置预置勾选，升级后可改变功能开关。
        feat = tk.LabelFrame(f, text="功能选项", padx=10, pady=6)
        feat.grid(row=7, column=0, columnspan=3, sticky="we", pady=(4, 8))
        # 定时提醒：久坐提醒；将来追加“待办提醒”可在其后添加复选框
        tk.Label(feat, text="定时提醒：").grid(
            row=0, column=0, sticky="w", pady=(0, 4))
        tk.Checkbutton(feat, text="久坐提醒",
                       variable=self.opt_health_reminder).grid(
            row=0, column=1, sticky="w", pady=(0, 4), padx=(0, 14))
        tk.Label(feat, text="AI 助手辅助：").grid(
            row=1, column=0, sticky="w", pady=(0, 2))
        ai_opts = tk.Frame(feat)
        ai_opts.grid(row=1, column=1, columnspan=2, sticky="w", pady=(0, 2))
        tk.Checkbutton(ai_opts, text="Nanobot",
                       variable=self.opt_ai_nanobot).pack(side="left", padx=(0, 14))
        tk.Checkbutton(ai_opts, text="Claude Code",
                       variable=self.opt_ai_claude).pack(side="left", padx=(0, 14))
        tk.Checkbutton(ai_opts, text="Codex",
                       variable=self.opt_ai_codex).pack(side="left", padx=(0, 14))
        tk.Checkbutton(ai_opts, text="OpenCode",
                       variable=self.opt_ai_opencode).pack(side="left")

        self.status = tk.StringVar(value="")
        if self.is_upgrade and self.detected_dir:
            self.status.set("检测到已安装版本（" + self.detected_dir
                            + "），将升级到 v" + get_version() + " 并保留原有配置。")
        tk.Label(f, textvariable=self.status, fg="#555555").grid(
            row=8, column=0, columnspan=3, sticky="w", pady=(0, 6))

        btns = tk.Frame(f)
        btns.grid(row=9, column=0, columnspan=3, sticky="e")
        tk.Button(btns, text="取消", width=10,
                  command=self._cancel).pack(side="right", padx=4)
        self.install_btn = tk.Button(btns,
                                     text=("升级" if self.is_upgrade else "安装"),
                                     width=10, command=self._install)
        self.install_btn.pack(side="right")
        self.install_btn.config(state="disabled")
        self.opt_agree.trace_add("write", self._on_agree_changed)

    def _on_agree_changed(self, *args):
        self.install_btn.config(
            state="normal" if self.opt_agree.get() else "disabled")

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.install_dir.get())
        if d:
            self.install_dir.set(d)

    def _cancel(self):
        self.destroy()

    def _set_status(self, msg):
        self.status.set(msg)
        self.update_idletasks()

    def _stop_old_instance(self, target):
        """升级时关闭目标目录可能正在运行的 aipet.exe，释放文件锁。"""
        exe = os.path.normpath(target)
        if not os.path.exists(exe):
            return
        try:
            subprocess.run(["taskkill", "/IM", "aipet.exe", "/T", "/F"],
                           capture_output=True, timeout=10)
        except Exception:
            pass

    def _install(self):
        if self.busy:
            return
        self.busy = True
        if not self.opt_agree.get():
            messagebox.showwarning("提示", "请先阅读并同意许可协议。",
                                   parent=self)
            self.busy = False
            return
        dst = self.install_dir.get().strip()
        if not dst:
            messagebox.showerror("错误", "请选择安装位置。", parent=self)
            self.busy = False
            return
        target = os.path.join(dst, "aipet.exe")
        try:
            self._set_status("正在复制文件...")
            self.update()
            src = app_src_dir()
            if not os.path.isdir(src):
                raise RuntimeError(
                    "未找到应用文件（缺少 dist/aipet）。请先构建应用。")
            os.makedirs(dst, exist_ok=True)
            # 复制应用目录；不覆盖已有用户配置
            for name in os.listdir(src):
                s = os.path.join(src, name)
                d = os.path.join(dst, name)
                if os.path.isdir(s):
                    shutil.copytree(s, d, dirs_exist_ok=True)
                else:
                    try:
                        shutil.copy2(s, d)
                    except PermissionError:
                        # 升级时旧 aipet.exe / uninstaller.exe 可能仍在运行，
                        # 先关闭再重试一次，避免文件被锁导致升级失败。
                        if self.is_upgrade and name in ("aipet.exe", "uninstaller.exe"):
                            self._set_status("正在关闭旧版本以完成升级...")
                            self._stop_old_instance(target)
                            shutil.copy2(s, d)
                        else:
                            raise
            # 确保目标目录有 config.json：升级时保留用户已有配置；
            # 全新安装则从内置默认配置复制一份。
            cfg_src = os.path.join(src, "_internal", "config.json")
            cfg_dst = os.path.join(dst, "config.json")
            if os.path.exists(cfg_src) and not os.path.exists(cfg_dst):
                shutil.copy2(cfg_src, cfg_dst)
            # 无论全新安装还是升级，都按安装界面的功能勾选同步 enabled 标志，
            # 使升级时也能开启/关闭对应功能。
            if os.path.exists(cfg_dst):
                self._apply_feature_selection(cfg_dst)

            if self.opt_shortcut.get():
                self._set_status("正在创建桌面快捷方式...")
                self._create_shortcut(target)

            if self.opt_startup.get():
                self._set_status("正在设置开机自启...")
                self._set_startup(target, True)

            self._set_status("正在写入卸载信息...")
            self._write_uninstall(dst, target)

            # 记录安装目录供程序使用
            try:
                import winreg
                k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_APP)
                winreg.SetValueEx(k, "InstallDir", 0, winreg.REG_SZ, dst)
                winreg.CloseKey(k)
            except Exception:
                pass

            if self.opt_launch.get():
                self._set_status("正在启动...")
                try:
                    subprocess.Popen([target], creationflags=0x08000000)
                except Exception as e:
                    messagebox.showwarning("提示",
                        "安装完成，但启动失败：" + str(e), parent=self)

            self._set_status("完成。")
            messagebox.showinfo("安装完成",
                                APP_NAME + " 已安装到：\n" + dst,
                                parent=self)
            self.destroy()
        except Exception as e:
            messagebox.showerror("安装失败", str(e), parent=self)
            self._set_status("失败：" + str(e)[:120])
            self.busy = False

    def _apply_feature_selection(self, cfg_path):
        """依据安装界面的功能勾选，设置 config.json 中各菜单项的 enabled 标志。

        未勾选的功能 -> enabled=false（运行时对应菜单隐藏）。
        全新安装和升级时都会调用：升级模式下按用户的新勾选同步 enabled，
        从而允许在升级过程中开启/关闭功能。
        """
        try:
            with open(cfg_path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except Exception:
            return
        menus = cfg.get("menus", [])
        health_on = self.opt_health_reminder.get()
        ai_map = {
            "Nanobot": self.opt_ai_nanobot.get(),
            "Claude Code": self.opt_ai_claude.get(),
            "Codex": self.opt_ai_codex.get(),
            "OpenCode": self.opt_ai_opencode.get(),
        }
        changed = False
        for m in menus:
            name = m.get("name", "")
            if m.get("type") == "health_reminder":
                if bool(m.get("enabled", True)) != bool(health_on):
                    m["enabled"] = bool(health_on)
                    changed = True
            elif name in ai_map:
                # 按名称匹配 AI 助手（含带 endpoint 的 Nanobot），不依赖 type 字段。
                if bool(m.get("enabled", True)) != bool(ai_map[name]):
                    m["enabled"] = bool(ai_map[name])
                    changed = True
        if changed:
            try:
                with open(cfg_path, "w", encoding="utf-8") as fh:
                    json.dump(cfg, fh, ensure_ascii=False, indent=2)
            except Exception:
                pass

    def _create_shortcut(self, target):
        link = os.path.join(desktop_path(), SHORTCUT_NAME)
        # 规范化反斜杠，避免 IconLocation 出现 D:/x\y.exe 这类混用分隔符
        target = os.path.normpath(target)
        work = os.path.dirname(target)
        vbs = tempfile.NamedTemporaryFile(delete=False, suffix=".vbs").name

        # VBScript 对 UTF-8 无 BOM 的中文支持不稳定，统一用 UTF-16 LE + BOM，
        # wscript.exe 可正确解析，避免快捷方式名称/描述乱码。
        script = (
            'Set oWS = WScript.CreateObject("WScript.Shell")\n'
            'Set oLink = oWS.CreateShortcut("' + link + '")\n'
            'oLink.TargetPath = "' + target + '"\n'
            'oLink.WorkingDirectory = "' + work + '"\n'
            'oLink.Description = "' + APP_NAME + '"\n'
            'oLink.IconLocation = "' + target + ',0"\n'
            'oLink.Save\n'
        )
        try:
            with open(vbs, "wb") as fh:
                fh.write(b"\xff\xfe")
                fh.write(script.encode("utf-16-le"))
            subprocess.run(["wscript", vbs], check=False)
        finally:
            try:
                os.remove(vbs)
            except Exception:
                pass

    def _set_startup(self, target, on):
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN, 0,
                           winreg.KEY_SET_VALUE)
        if on:
            # 规范化为反斜杠路径，避免 D:/x\y.exe 这类混用分隔符导致
            # 登录时 Run 键解析失败、开机自启不生效。
            clean = os.path.normpath(target)
            winreg.SetValueEx(k, STARTUP_VAL, 0, winreg.REG_SZ,
                              '"' + clean + '"')
        else:
            try:
                winreg.DeleteValue(k, STARTUP_VAL)
            except FileNotFoundError:
                pass
        winreg.CloseKey(k)

    def _write_uninstall(self, dst, target):
        # 卸载由图形化 uninstaller.exe 完成（随 app 一起部署到安装目录）。
        # 不再生成无窗口的 uninstall.bat。
        uninstaller = os.path.join(dst, "uninstaller.exe")
        try:
            import winreg
            k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_UNINSTALL)
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ,
                              '"' + uninstaller + '"')
            winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ,
                              get_version())
            winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, dst)
            winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
            winreg.CloseKey(k)
        except Exception:
            pass


def main():
    Installer().mainloop()


if __name__ == "__main__":
    main()
