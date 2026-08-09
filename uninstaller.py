# -*- coding: utf-8 -*-
# 桌面AI助理 - 图形化卸载程序
# 由「设置 -> 应用」或控制面板中的卸载项调用。
# 提供卸载确认窗口、将被移除项目的清单、以及「保留个人配置」选项。
#
# 自删除机制（参考常规 Windows 卸载器，如 NSIS / Inno Setup / msiexec 的临时副本模式）：
#   - 本程序通常部署在安装目录内（uninstaller.exe），运行时自身被占用，
#     无法直接删除自己所在的文件。
#   - 因此启动后若发现自身仍在安装目录内，会先把自身复制到 %TEMP%，
#     再由 TEMP 中的副本继续执行卸载，从而可以干净地删除安装目录
#     （含原 uninstaller 副本）。
#   - 卸载完成后，TEMP 副本通过 MoveFileEx（重启后清理，兜底）+
#     隐藏 bat（即时清理）删除自己。

import os
import sys
import shutil
import tempfile
import subprocess
import ctypes
import time
import tkinter as tk
from tkinter import messagebox

APP_NAME = "桌面AI助理"
SHORTCUT_NAME = "桌面AI助理.lnk"
STARTUP_VAL = "DesktopAIPet"
REG_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\DesktopAIPet"
REG_APP = r"Software\zhixinglvren\desktop-aipet"

# 主程序可执行文件名（用于进程检测）
APP_EXE = "aipet.exe"
# 子进程无窗口标志（Windows）
CREATE_NO_WINDOW = 0x08000000


def is_frozen():
    return getattr(sys, "frozen", False)


def res_dir():
    """图标等资源所在目录：冻结态在 _MEIPASS，开发态在脚本目录。"""
    if is_frozen():
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


# ------------------------- 进程检测与关闭 -------------------------
def is_process_running(name):
    """检测指定镜像名（如 aipet.exe）是否正在运行。"""
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq %s" % name],
            creationflags=CREATE_NO_WINDOW, text=True, errors="ignore")
        return name.lower() in out.lower()
    except Exception:
        return False


def kill_process(name, force=False):
    """结束进程（含子进程树）。force=True 时强制终止。"""
    cmd = ["taskkill", "/IM", name, "/T"]
    if force:
        cmd.append("/F")
    try:
        subprocess.run(cmd, creationflags=CREATE_NO_WINDOW,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def wait_process_exit(name, timeout=8.0):
    """轮询等待进程退出，返回是否已在超时内退出。"""
    step = 0.3
    waited = 0.0
    while waited < timeout:
        if not is_process_running(name):
            return True
        time.sleep(step)
        waited += step
    return not is_process_running(name)


# ------------------------- 自删除（临时副本） -------------------------
def running_in_temp():
    """当前进程是否在 %TEMP% 中运行（即已是副本）。"""
    if not is_frozen():
        return False
    return sys.executable.lower().startswith(tempfile.gettempdir().lower())


def should_relaunch():
    """原始 uninstaller 位于安装目录内（非 TEMP），需先重启到 TEMP 副本，
    以便能删除安装目录中的 uninstaller 自身副本。"""
    if not is_frozen():
        return False
    exe = sys.executable.lower()
    if not exe.endswith("uninstaller.exe"):
        return False
    return not exe.startswith(tempfile.gettempdir().lower())


def relaunch_from_temp():
    """复制自身到 %TEMP% 并从副本继续，当前进程退出。"""
    exe = sys.executable
    dst = os.path.join(tempfile.gettempdir(),
                       "desktop-aipet-uninstaller-%d.exe" % os.getpid())
    try:
        shutil.copy2(exe, dst)
    except Exception:
        return  # 复制失败则原地运行（至少能卸其他文件）
    try:
        subprocess.Popen([dst])
    except Exception:
        try:
            os.remove(dst)
        except Exception:
            pass
        return
    sys.exit(0)


def schedule_self_delete(path):
    """安排删除自身（位于 TEMP 的副本）：
    优先用隐藏 bat 即时删除；同时注册 MoveFileEx 重启后清理作为兜底。"""
    # 兜底：重启后删除（Windows 官方 pending rename 机制）
    try:
        MOVEFILE_DELAY_UNTIL_REBOOT = 0x4
        ctypes.windll.kernel32.MoveFileExW(path, None,
                                           MOVEFILE_DELAY_UNTIL_REBOOT)
    except Exception:
        pass
    # 即时：隐藏窗口运行 bat，等待本进程退出后删除 path 与 bat 自身
    try:
        bat = os.path.join(tempfile.gettempdir(),
                           "desktop-aipet-cleanup-%d.bat" % os.getpid())
        with open(bat, "w", encoding="utf-8") as fh:
            fh.write("@echo off\r\n")
            fh.write("ping 127.0.0.1 -n 2 >nul\r\n")
            fh.write('del /f /q "%s"\r\n' % path)
            fh.write('del /f /q "%~f0"\r\n')
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        subprocess.Popen(["cmd", "/c", bat], startupinfo=si,
                         creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass


# ------------------------- 其余工具函数 -------------------------
def desktop_path():
    try:
        buf = ctypes.create_unicode_buffer(4096)
        # CSIDL_DESKTOP = 0x10
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf)
        if buf.value:
            return buf.value
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def read_install_dir():
    """从安装时写入的注册表项定位安装目录；失败则回退到 exe 所在目录。"""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_APP)
        v, _ = winreg.QueryValueEx(k, "InstallDir")
        winreg.CloseKey(k)
        if v and os.path.isdir(v):
            return v
    except Exception:
        pass
    return os.path.dirname(os.path.abspath(sys.executable))


def del_reg_value(path, name=None):
    import winreg
    try:
        if name is None:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        else:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0,
                               winreg.KEY_SET_VALUE)
            try:
                winreg.DeleteValue(k, name)
            except FileNotFoundError:
                pass
            winreg.CloseKey(k)
    except Exception:
        pass


def remove_path(p):
    try:
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
    except Exception:
        pass


class Uninstaller(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("卸载 桌面AI助理")
        self.resizable(False, False)
        self._set_window_icon()
        self.install_dir = read_install_dir()
        self.busy = False
        self.opt_keep = tk.BooleanVar(value=True)
        self._build()
        self._center_window()

    def _set_window_icon(self):
        try:
            ico = os.path.join(res_dir(), "app.ico")
            if os.path.exists(ico):
                self.iconbitmap(ico)
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

    def _build(self):
        f = tk.Frame(self, padx=18, pady=16)
        f.pack()

        tk.Label(f, text="卸载 桌面AI助理",
                 font=("Microsoft YaHei UI", 14, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        lines = [
            "您正在卸载 桌面AI助理。以下项目将被移除：",
            "",
            "  • 程序安装目录：",
            "      " + (self.install_dir or "（未找到）"),
            "  • 桌面快捷方式「" + SHORTCUT_NAME + "」",
            "  • Windows 开机自动启动项",
            "  • 控制面板中的卸载记录",
        ]
        tk.Label(f, text="\n".join(lines), justify="left").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))

        tk.Checkbutton(
            f,
            text="保留我的个人配置（宠物形象、健康监控、自定义设置等）",
            variable=self.opt_keep).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(0, 10))

        self.status = tk.StringVar(value="")
        tk.Label(f, textvariable=self.status, fg="#555555").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(0, 8))

        btns = tk.Frame(f)
        btns.grid(row=4, column=0, columnspan=2, sticky="e")
        tk.Button(btns, text="取消", width=10,
                  command=self._cancel).pack(side="right", padx=4)
        tk.Button(btns, text="卸载", width=10,
                  command=self._uninstall).pack(side="right")

    def _set_status(self, s):
        self.status.set(s)
        self.update()

    def _cancel(self):
        if not self.busy:
            self.destroy()

    def _ensure_app_stopped(self):
        """确保主程序已退出；返回 True 表示可以继续卸载。"""
        if not is_process_running(APP_EXE):
            return True
        self._set_status("正在关闭 %s..." % APP_NAME)
        # 先礼貌请求退出（不带 /F），再等待
        kill_process(APP_EXE)
        if wait_process_exit(APP_EXE, timeout=8.0):
            return True
        # 仍在运行：提示用户是否强制结束
        if messagebox.askyesno(
                "进程仍在运行",
                "%s 仍在运行，无法彻底卸载。\n是否强制结束该进程？" % APP_NAME,
                parent=self):
            kill_process(APP_EXE, force=True)
            if wait_process_exit(APP_EXE, timeout=5.0):
                return True
        messagebox.showwarning(
            "无法卸载",
            "未能关闭 %s，请手动结束该程序后重试卸载。" % APP_NAME,
            parent=self)
        return False

    def _uninstall(self):
        if self.busy:
            return
        self.busy = True

        if not self.opt_keep.get():
            if not messagebox.askyesno(
                    "确认",
                    "您选择不保留个人配置，\n所有设置将永久删除且无法恢复。\n确定继续吗？",
                    parent=self):
                self.busy = False
                return

        dst = self.install_dir
        if not dst or not os.path.isdir(dst):
            messagebox.showerror("错误", "未找到安装目录：\n" + str(dst),
                                 parent=self)
            self.busy = False
            return

        # 关键：先确认主程序已退出，避免卸载不干净
        if not self._ensure_app_stopped():
            self.busy = False
            return

        try:
            self._set_status("正在移除...")
            # 1) 开机自动启动项
            del_reg_value(REG_RUN, STARTUP_VAL)
            # 2) 控制面板卸载记录
            del_reg_value(REG_UNINSTALL)
            # 3) 安装目录记录
            del_reg_value(REG_APP)
            # 4) 桌面快捷方式
            lnk = os.path.join(desktop_path(), SHORTCUT_NAME)
            if os.path.exists(lnk):
                try:
                    os.remove(lnk)
                except Exception:
                    pass

            # 5) 程序文件
            if self.opt_keep.get():
                # 保留 config.json：先备份，删除其余，再放回。
                cfg = os.path.join(dst, "config.json")
                backup = None
                if os.path.exists(cfg):
                    backup = os.path.join(
                        tempfile.gettempdir(),
                        "desktop-aipet-config-backup.json")
                    try:
                        shutil.copy2(cfg, backup)
                    except Exception:
                        backup = None
                for name in os.listdir(dst):
                    if name == "config.json":
                        continue
                    remove_path(os.path.join(dst, name))
                if backup and os.path.exists(backup):
                    try:
                        shutil.copy2(backup, cfg)
                        os.remove(backup)
                    except Exception:
                        pass
            else:
                for name in os.listdir(dst):
                    remove_path(os.path.join(dst, name))
                try:
                    os.rmdir(dst)
                except Exception:
                    pass

            self._set_status("完成。")
            messagebox.showinfo("卸载完成", APP_NAME + " 已成功卸载。",
                                parent=self)

            # 若运行的是 TEMP 副本，则安排删除自身（原始 uninstaller 已随
            # 安装目录被清空而一并删除）。
            if running_in_temp():
                schedule_self_delete(sys.executable)

            self.destroy()
        except Exception as e:
            messagebox.showerror("卸载失败", str(e), parent=self)
            self.busy = False


def main():
    if should_relaunch():
        relaunch_from_temp()
        return
    Uninstaller().mainloop()


if __name__ == "__main__":
    main()
