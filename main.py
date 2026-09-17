"""
AI叙事RPG - 入口程序
"""
import sys
import os
import traceback
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

# 确保src在路径中（打包成 exe 后由 PyInstaller 自己处理，不再改 sys.path）
if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

# DPI 感知：Windows 显示缩放（如125%）下若进程不感知，1920×1080逻辑窗口会被
# 系统虚拟化放大（2400×1350物理），底部带整段溢出屏幕下沿（2026-08-06 上机实测
# 底部带不可见的叠加嫌疑；地图查看器 map_test/proto_map_viewer.py 已踩过同坑）。
# 必须在创建任何窗口之前调用；感知后 winfo_screenwidth 返回物理像素，居中计算无需改。
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 1 = PROCESS_SYSTEM_DPI_AWARE
except Exception:
    pass  # 非Windows或老系统忽略

from src.ui import FONT_FAMILY, BASE_FONT_SIZE_SCALED, DEFAULT_RESOLUTION, parse_resolution

# 窗口外框的非客户区总高度（2026-08-06 本机实测：标题栏+上边框31px + 下边框8px）
# 几何定位语义（同日本机实测）：geometry +x+y 定位的是【外框左上角】（含标题栏），
# 所以高度适配与居中都必须按"外框=客户区+39"整体计算
WINDOW_CHROME_H = 39


def compute_locked_geometry(cfg_w, cfg_h, work_l, work_t, work_r, work_b):
    """分辨率锁定的最终 geometry 四元组 (w, h, x, y)（纯函数，可独立冒烟）。
    窗口整体（外框）必须嵌进屏幕工作区（已扣任务栏），否则 pack(side=BOTTOM)
    钉在客户区最底的底部带会被屏幕下沿/任务栏切掉
    （2026-08-06 上机实测：1440×1080 底带第二行卡片与游戏助手被切）。
    高度适配：h = min(配置高, 工作区高 - 外框高39)；宽度保底不超工作区宽；
    按外框在工作区内居中（y ≥ 工作区顶）。"""
    work_w, work_h = work_r - work_l, work_b - work_t
    w = min(cfg_w, work_w)
    h = min(cfg_h, work_h - WINDOW_CHROME_H)
    x = work_l + max(0, (work_w - w) // 2)
    y = work_t + max(0, (work_h - h - WINDOW_CHROME_H) // 2)
    return w, h, x, y
from src.ui.main_menu import MainMenu
from src.ui.new_game_dialog import NewGameDialog
from src.ui.game_window import GameWindow
from src.save_manager import list_saves
from src.config import get_config


class Application:
    def __init__(self):
        # 创建主窗口（尺寸由分辨率锁定统一设定，不再自适应）
        self.root = ttk.Window(
            title="AI Narrative RPG",
            themename="darkly",
            resizable=(True, True)
        )

        # 全局基准字体在此统一设定一次（P2：各窗口不再单独修改全局命名字体）
        tkfont.nametofont("TkDefaultFont").configure(family=FONT_FAMILY, size=BASE_FONT_SIZE_SCALED)
        tkfont.nametofont("TkTextFont").configure(family=FONT_FAMILY, size=BASE_FONT_SIZE_SCALED)

        # 程序启动即锁死窗口分辨率（2026-08-05 用户定案：全程统一，root生命周期只做一次）
        self._apply_resolution_lock()

        self.current_frame = None
        self.game_window = None

        # 修复：拦截关窗请求，回合生成中禁止直接退出（避免后台线程写已销毁UI/存档写一半）
        self.root.protocol("WM_DELETE_WINDOW", self.on_root_close)
        # 修复：tk回调异常全局钩子，避免异常只打印到控制台而用户无感知
        self.root.report_callback_exception = self._on_tk_callback_error

        self.show_main_menu()
        # 无可用密钥时引导去设置（延迟到主菜单渲染完，否则弹窗会盖在黑屏上）
        self.root.after(400, self._prompt_api_key_if_missing)

    def _prompt_api_key_if_missing(self):
        """首次启动引导（2026-09-17）：一个可用密钥都没有时直接引导去设置。
        否则用户要玩到第一次生成叙事才撞见"未设置密钥"，那时已不知道该去哪配。"""
        cfg = get_config()
        provider = cfg.get("models", "main", "provider", default="deepseek")
        if cfg.get_api_key(provider):
            return  # 本地推理服务的占位符密钥也算已配置
        if not messagebox.askyesno(
                "AI Service Setup Required",
                "No API key for the AI service is configured yet, so the game cannot "
                "generate narrative.\n\n"
                "Open settings now to enter one?\n"
                "(You can also click \"Settings\" in the top-right of the main menu later.)"):
            return
        try:
            self.current_frame.open_settings()
        except Exception:
            traceback.print_exc()

    def _get_work_area(self):
        """Windows屏幕工作区矩形（已扣任务栏）：返回 (left, top, right, bottom)。
        SPI_GETWORKAREA 查询失败时兜底：全屏尺寸高度扣经验值110px（任务栏~48+外框~39+余量）"""
        try:
            import ctypes

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
            rect = RECT()
            SPI_GETWORKAREA = 0x0048
            if ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
                return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            pass
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight() - 110

    def _apply_resolution_lock(self):
        """启动即锁定分辨率：读ui.resolution配置（解析失败兜底4:3默认）
        → 高度适配屏幕工作区（窗口整体含标题栏嵌进工作区，底部带不再被切）
        → geometry设定尺寸+工作区内居中 → resizable(False,False)。
        仅在root创建后调用一次（root生命周期内不重复执行）"""
        res_text = get_config().get("ui", "resolution", default=DEFAULT_RESOLUTION)
        cfg_w, cfg_h = parse_resolution(res_text)
        work = self._get_work_area()
        w, h, x, y = compute_locked_geometry(cfg_w, cfg_h, *work)
        if (w, h) != (cfg_w, cfg_h):
            print(f"[UI] resolution {cfg_w}x{cfg_h} exceeds the work area; height adjusted to {w}x{h}"
                  f" (work area {work[2] - work[0]}x{work[3] - work[1]}, reserving {WINDOW_CHROME_H}px for window chrome)")
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.resizable(False, False)

    def show_main_menu(self):
        """显示主菜单"""
        self._clear_frame()
        self.current_frame = MainMenu(
            self.root,
            on_new_game=self.on_new_game,
            on_continue_game=self.on_continue_game
        )

    def _clear_frame(self):
        """清除当前框架（destroy 前判 winfo_exists，防止重复 destroy 已销毁控件崩溃）"""
        try:
            if self.current_frame:
                if hasattr(self.current_frame, 'frame'):
                    f = self.current_frame.frame
                    if f and f.winfo_exists():
                        f.destroy()
                elif hasattr(self.current_frame, 'destroy'):
                    self.current_frame.destroy()
                self.current_frame = None
            if self.game_window:
                if hasattr(self.game_window, 'frame'):
                    f = self.game_window.frame
                    if f and f.winfo_exists():
                        f.destroy()
                self.game_window = None
        except tk.TclError:
            pass  # 控件已销毁，静默

    def on_new_game(self, save_name=None):
        """新游戏"""
        if save_name is None:
            # 寻找第一个空槽位
            saves = list_saves()
            empty = [s for s in saves if not s["exists"]]
            if not empty:
                self._show_error("All save slots are full (max 10). Delete an old save first.")
                return
            save_name = empty[0]["name"]

        def on_created(name):
            self._start_game(name)

        NewGameDialog(self.root, save_name, on_created)

    def on_continue_game(self, save_name):
        """继续游戏"""
        self._start_game(save_name)

    def _start_game(self, save_name):
        """启动游戏窗口"""
        self._clear_frame()
        self.game_window = GameWindow(
            self.root,
            save_name=save_name,
            on_return_menu=self.show_main_menu
        )
        self.current_frame = self.game_window

    def _show_error(self, message):
        from tkinter import messagebox
        messagebox.showerror("Error", message)

    def on_root_close(self):
        """主窗口关闭请求：游戏处理中禁止退出，其余情况确认后退出"""
        gw = self.game_window
        try:
            if gw and gw.frame.winfo_exists() and gw.is_processing():
                messagebox.showinfo("Please wait", "A turn is currently being generated. "
                                                   "Wait for it to finish before quitting.")
                return
        except tk.TclError:
            pass  # 窗口已销毁，直接走正常退出流程
        if messagebox.askyesno("Confirm", "Are you sure you want to quit the game?"):
            self.root.destroy()

    def _on_tk_callback_error(self, exc, val, tb):
        """tk回调异常全局处理：打印堆栈并尽量在界面上提示用户"""
        traceback.print_exception(exc, val, tb)
        gw = self.game_window
        try:
            if gw and gw.frame.winfo_exists():
                gw.notify_callback_error(f"UI callback error: {val}")
                return
        except tk.TclError:
            pass
        try:
            messagebox.showerror("Error", f"UI callback error: {val}")
        except tk.TclError:
            pass  # 主窗口已销毁时无法再弹窗

    def run(self):
        self.root.mainloop()


def _crash_log_path():
    """崩溃日志位置：优先程序目录，不可写则退回用户目录"""
    import pathlib
    base = pathlib.Path(sys.executable).parent if getattr(sys, "frozen", False) \
        else pathlib.Path(__file__).parent
    try:
        p = base / "crash.log"
        with open(p, "a", encoding="utf-8"):
            pass
        return p
    except OSError:
        return pathlib.Path.home() / "ai_rpg_crash.log"


if __name__ == "__main__":
    try:
        app = Application()
        app.run()
    except Exception:
        # 打包成 exe 后没有控制台，异常打印等于丢弃；落盘 + 弹窗才找得到原因
        import datetime
        log = _crash_log_path()
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"\n{'=' * 60}\n{datetime.datetime.now()}\n")
                traceback.print_exc(file=f)
        except Exception:
            pass
        try:
            messagebox.showerror(
                "Startup Failed",
                "An error occurred while starting the program. Details have been written to:\n"
                f"{log}\n\nPlease send this file to the developer.")
        except Exception:
            pass
        raise
