"""
主菜单UI
"""
import contextlib
import os
import subprocess
import sys
import tkinter as tk
import webbrowser
from tkinter import messagebox
import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from . import (FONT_FAMILY, RESOLUTION_OPTIONS, DEFAULT_RESOLUTION, place_window,
               SCALE, font_size, PROVIDER_PRESETS, LOCAL_PLACEHOLDER_KEY)
from ..save_manager import list_saves, delete_save
from ..config import get_config


def _s(v):
    """尺寸缩放（2026-08-15）：× SCALE，1800×1350 时不变"""
    return int(v * SCALE)


def _restart_app():
    """自动重启程序（2026-08-15）：重新执行自身，当前进程退出。
    仅当分辨率等需重启生效的设置变化时调用"""
    try:
        if getattr(sys, "frozen", False):
            # 打包成 exe：sys.executable 就是 exe 本身，直接重新拉起
            subprocess.Popen([sys.executable])
        else:
            # main.py 在项目根（src/ui/ 上溯2级 → 根 → main.py）
            script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "main.py"))
            if not os.path.exists(script):
                raise FileNotFoundError(f"main.py 不存在: {script}")
            subprocess.Popen([sys.executable, script], cwd=os.path.dirname(script))
    except Exception as e:
        messagebox.showerror("重启失败", f"无法自动重启: {e}")
        return
    os._exit(0)  # 立即退出当前进程（新进程已启动）


class SettingsDialog:
    """设置对话框"""

    def __init__(self, parent):
        self.window = ttk.Toplevel(parent)
        self.window.title("设置")
        self.window.transient(parent)
        self.window.grab_set()
        # 相对父窗口居中，尺寸随屏幕自适应且不越界
        # 2026-08-14 用户定：宽度改为原来的1.5倍（520→780）
        # 2026-09-17 高度 520→660：多厂商改造加了厂商/地址两行与提示，
        # 内容实测高 602px，520 会把底部按钮挤到滚动区外
        place_window(self.window, _s(780), _s(660), min_w=_s(420), min_h=_s(400), parent=parent)

        self.cfg = get_config()
        self.build_ui()

    def build_ui(self):
        # Canvas + Scrollbar 确保内容可滚动
        canvas = tk.Canvas(self.window, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.window, orient=VERTICAL, command=canvas.yview)
        scroll_frame = ttk.Frame(canvas, padding=_s(20))

        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        self._canvas_win = canvas.create_window((0, 0), window=scroll_frame, anchor=NW)
        # 内容宽度跟随窗口宽度（原来写死480，窗口拉宽后留白）
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(self._canvas_win, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side=RIGHT, fill=Y)
        canvas.pack(side=TOP, fill=BOTH, expand=YES)

        # 滚轮滚动内容（2026-08-14）：鼠标在设置界面任意处滚动时上下翻页。
        # Combobox 上滚轮不改变选中项、只滚动内容（见下方各 Combobox 绑定）。
        self._scroll_canvas = canvas
        def _on_mousewheel(event):
            try:
                canvas.yview_scroll(int(-event.delta / 120), "units")
            except tk.TclError:
                pass
            return "break"  # 阻止进一步传播（避免 Combobox 默认换选项）
        canvas.bind("<MouseWheel>", _on_mousewheel)
        canvas.bind("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))  # Linux上滚
        canvas.bind("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))   # Linux下滚

        frame = scroll_frame

        # ===== API 设置（2026-09-17 多厂商：全部走 OpenAI 兼容协议）=====
        ttk.Label(frame, text="API 设置").pack(anchor=W, pady=(_s(0), _s(10)))

        api_frame = ttk.Frame(frame)
        api_frame.pack(fill=X, pady=_s(5))

        self._provider_keys = list(PROVIDER_PRESETS.keys())

        # 厂商
        row_p = ttk.Frame(api_frame)
        row_p.pack(fill=X, pady=_s(2))
        ttk.Label(row_p, text="API 厂商:", width=_s(12)).pack(side=LEFT)
        self.provider_box = ttk.Combobox(
            row_p, state="readonly",
            values=[PROVIDER_PRESETS[k]["label"] for k in self._provider_keys])
        self.provider_box.pack(side=LEFT, fill=X, expand=YES, padx=(_s(5), _s(0)))
        self._bind_wheel(self.provider_box)
        self.provider_box.bind("<<ComboboxSelected>>", self.on_provider_change)

        # API 地址
        row_b = ttk.Frame(api_frame)
        row_b.pack(fill=X, pady=_s(2))
        ttk.Label(row_b, text="API 地址:", width=_s(12)).pack(side=LEFT)
        self.api_base_entry = ttk.Entry(row_b)
        self.api_base_entry.pack(side=LEFT, fill=X, expand=YES, padx=(_s(5), _s(0)))

        # 密钥（内容随厂商切换，各厂商密钥分别记忆）
        row_k = ttk.Frame(api_frame)
        row_k.pack(fill=X, pady=_s(2))
        ttk.Label(row_k, text="API 密钥:", width=_s(12)).pack(side=LEFT)
        self.api_key_entry = ttk.Entry(row_k, show="*")
        self.api_key_entry.pack(side=LEFT, fill=X, expand=YES, padx=(_s(5), _s(0)))
        ttk.Button(row_k, text="获取密钥", bootstyle=(INFO, OUTLINE), width=_s(9),
                   command=self.on_open_key_url).pack(side=LEFT, padx=(_s(5), _s(0)))

        ttk.Label(api_frame, text="密钥只保存在本机用户目录的 .ai_rpg_config.json，不会上传到任何地方",
                  foreground="gray").pack(anchor=W, pady=(_s(4), _s(0)))

        # 初始化厂商/地址/密钥（读当前配置）
        cur = self.cfg.get("models", "main", "provider", default="deepseek")
        if cur not in PROVIDER_PRESETS:
            cur = "custom"  # 配置里是旧厂商名（如已移除的 anthropic）→ 归入自定义
        self._cur_provider = cur
        self._key_cache = {k: (self.cfg.get("api_keys", k, default="") or "")
                           for k in set(self._provider_keys) | set(self.cfg.get("api_keys", default={}) or {})}
        saved_base = self.cfg.get("models", "main", "api_base", default="")
        self._base_cache = {cur: saved_base} if saved_base else {}
        self._model_cache = {cur: {
            "main": self.cfg.get("models", "main", "model", default=""),
            "light": self.cfg.get("models", "lightweight", "model", default=""),
        }}
        self.provider_box.set(PROVIDER_PRESETS[cur]["label"])

        # 模型选择（建议列表随厂商变化，可直接手动输入）
        ttk.Label(frame, text="模型设置").pack(anchor=W, pady=(_s(15), _s(10)))

        model_frame = ttk.Frame(frame)
        model_frame.pack(fill=X, pady=_s(5))

        row1 = ttk.Frame(model_frame)
        row1.pack(fill=X, pady=_s(2))
        ttk.Label(row1, text="主力模型:", width=_s(12)).pack(side=LEFT)
        self.main_model = ttk.Combobox(row1)
        self.main_model.pack(side=LEFT, fill=X, expand=YES, padx=(_s(5), _s(0)))
        self._bind_wheel(self.main_model)

        row2 = ttk.Frame(model_frame)
        row2.pack(fill=X, pady=_s(2))
        ttk.Label(row2, text="轻量模型:", width=_s(12)).pack(side=LEFT)
        self.light_model = ttk.Combobox(row2)
        self.light_model.pack(side=LEFT, fill=X, expand=YES, padx=(_s(5), _s(0)))
        self._bind_wheel(self.light_model)

        ttk.Label(model_frame, text="模型名可直接手动输入；主力模型负责叙事，轻量模型负责判定/摘要等后台任务",
                  foreground="gray").pack(anchor=W, pady=(_s(4), _s(0)))

        self._load_provider_into_form(cur)

        # 分辨率选择（游戏窗口进入时按此锁死分辨率；选项常量易扩展，以后加2K只改RESOLUTION_OPTIONS）
        ttk.Label(frame, text="显示设置").pack(anchor=W, pady=(_s(15), _s(10)))

        res_frame = ttk.Frame(frame)
        res_frame.pack(fill=X, pady=_s(5))
        row3 = ttk.Frame(res_frame)
        row3.pack(fill=X, pady=_s(2))
        ttk.Label(row3, text="游戏分辨率:", width=_s(12)).pack(side=LEFT)
        self.resolution = ttk.Combobox(row3, values=RESOLUTION_OPTIONS, state="readonly")
        self.resolution.pack(side=LEFT, fill=X, expand=YES, padx=(_s(5), _s(0)))
        self.resolution.set(self.cfg.get("ui", "resolution", default=DEFAULT_RESOLUTION))
        self.resolution.bind("<MouseWheel>", lambda e: self._wheel_scroll(e))
        self.resolution.bind("<Button-4>", lambda e: self._wheel_scroll(e, -1))
        self.resolution.bind("<Button-5>", lambda e: self._wheel_scroll(e, 1))
        ttk.Label(row3, text="（重启程序后生效）", foreground="gray").pack(side=LEFT, padx=(_s(8), _s(0)))

        # 按钮栏（放在frame底部，确保可见）
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=X, pady=(_s(30), _s(0)))
        ttk.Button(btn_frame, text="测试连接", command=self.on_test_connection, bootstyle=INFO, width=_s(10)).pack(side=LEFT, padx=_s(5))
        ttk.Button(btn_frame, text="保存设置", command=self.on_save, bootstyle=INFO, width=_s(12)).pack(side=RIGHT, padx=_s(5))
        ttk.Button(btn_frame, text="取消", command=self.window.destroy, width=_s(8)).pack(side=RIGHT, padx=_s(5))

        # 滚轮在任意处滚动内容：递归绑定到内容区所有子控件（Combobox 已有专用绑定，其余用此兜底）
        def _bind_all(w):
            w.bind("<MouseWheel>", self._wheel_scroll)
            w.bind("<Button-4>", lambda e: self._wheel_scroll(e, -1))
            w.bind("<Button-5>", lambda e: self._wheel_scroll(e, 1))
            for child in w.winfo_children():
                _bind_all(child)
        _bind_all(scroll_frame)

    def _wheel_scroll(self, event, units=None):
        """滚轮滚动设置界面内容区（Combobox 上滚动内容、不切换选项）"""
        try:
            if units is not None:
                self._scroll_canvas.yview_scroll(units, "units")
            else:
                self._scroll_canvas.yview_scroll(int(-event.delta / 120), "units")
        except tk.TclError:
            pass
        return "break"  # 阻止默认行为（如 Combobox 切换选项）

    def _bind_wheel(self, widget):
        """滚轮停在控件上时滚动设置界面，不改变控件选中项（2026-08-14）"""
        widget.bind("<MouseWheel>", lambda e: self._wheel_scroll(e))
        widget.bind("<Button-4>", lambda e: self._wheel_scroll(e, -1))
        widget.bind("<Button-5>", lambda e: self._wheel_scroll(e, 1))

    def _default_key_for(self, key):
        """本地推理服务无需密钥，用占位符满足 API 层的非空校验；未知厂商一律空"""
        preset = PROVIDER_PRESETS.get(key)
        if preset is None or preset.get("needs_key", True):
            return ""
        return LOCAL_PLACEHOLDER_KEY

    def _stash_form(self):
        """把表单当前内容存进该厂商的缓存（切换厂商 / 保存 / 测试前调用）"""
        key = self._cur_provider
        if not key:
            return
        self._key_cache[key] = self.api_key_entry.get().strip()
        self._base_cache[key] = self.api_base_entry.get().strip()
        self._model_cache[key] = {"main": self.main_model.get().strip(),
                                  "light": self.light_model.get().strip()}

    def _load_provider_into_form(self, key):
        """把某厂商的地址/密钥/模型建议载入表单（有缓存用缓存，否则用预设）"""
        preset = PROVIDER_PRESETS[key]
        self.api_base_entry.delete(0, "end")
        self.api_base_entry.insert(0, self._base_cache.get(key, "") or preset["api_base"])
        self.api_key_entry.delete(0, "end")
        self.api_key_entry.insert(0, self._key_cache.get(key, "") or self._default_key_for(key))

        models = preset["models"]
        cached = self._model_cache.get(key, {})
        for widget, slot in ((self.main_model, "main"), (self.light_model, "light")):
            widget.configure(values=models)
            name = cached.get(slot, "")
            # 模型名是厂商专有的，沿用别家的名字会调不通 → 不在建议表里就取第一个
            widget.set(name if name in models else (models[0] if models else ""))

    def on_provider_change(self, event=None):
        """切换厂商：先存下当前输入，再载入新厂商的"""
        self._stash_form()
        key = self._provider_keys[self.provider_box.current()]
        self._cur_provider = key
        self._load_provider_into_form(key)

    def on_open_key_url(self):
        """打开当前厂商的密钥申请页"""
        url = PROVIDER_PRESETS[self._cur_provider].get("key_url", "")
        if not url:
            messagebox.showinfo("获取密钥", "该厂商没有固定的密钥申请页，请查阅其官方文档。", parent=self.window)
            return
        webbrowser.open(url)

    @contextlib.contextmanager
    def _temp_api_config(self, provider, model, api_base, key):
        """把界面上的厂商/模型/地址/密钥临时注入配置供实测，退出时原样还原（全程不落盘）"""
        data = self.cfg.data
        models = data.setdefault("models", {}).setdefault("main", {})
        keys = data.setdefault("api_keys", {})
        missing = object()
        old_fields = [models.get(f, missing) for f in ("provider", "model", "api_base")]
        old_key = keys.get(provider, missing)
        try:
            models["provider"], models["model"], models["api_base"] = provider, model, api_base
            keys[provider] = key
            yield
        finally:
            for field, val in zip(("provider", "model", "api_base"), old_fields):
                if val is missing:
                    models.pop(field, None)
                else:
                    models[field] = val
            if old_key is missing:
                keys.pop(provider, None)
            else:
                keys[provider] = old_key

    def on_save(self):
        old_res = self.cfg.get("ui", "resolution", default=DEFAULT_RESOLUTION)
        self._stash_form()
        provider = self._cur_provider
        preset = PROVIDER_PRESETS[provider]
        api_base = self._base_cache.get(provider, "").strip() or preset["api_base"]
        api_key = self._key_cache.get(provider, "").strip()
        cached = self._model_cache.get(provider, {})
        main_model = cached.get("main", "").strip()
        light_model = cached.get("light", "").strip() or main_model

        if not api_base:
            messagebox.showinfo("无法保存", "请填写 API 地址。", parent=self.window)
            return
        if not main_model:
            messagebox.showinfo("无法保存", "请填写主力模型名。", parent=self.window)
            return
        if preset.get("needs_key", True) and not api_key:
            if not messagebox.askyesno(
                    "尚未填写密钥",
                    f"还没有填写 {preset['label']} 的 API 密钥，保存后游戏无法生成叙事。\n仍要保存吗？",
                    parent=self.window):
                return
        if not api_key:
            api_key = LOCAL_PLACEHOLDER_KEY

        # 直接改内存字典后一次落盘（cfg.set 每次都会写盘，这里要写十几个键）
        data = self.cfg.data
        keys = data.setdefault("api_keys", {})
        for k in set(self._key_cache) | {provider}:
            keys[k] = self._key_cache.get(k, "").strip() or self._default_key_for(k)
        keys[provider] = api_key
        models = data.setdefault("models", {})
        for mt, model in (("main", main_model), ("lightweight", light_model)):
            models.setdefault(mt, {}).update(
                {"provider": provider, "model": model, "api_base": api_base})
        data.setdefault("ui", {})["resolution"] = self.resolution.get()
        self.cfg.save()

        new_res = self.resolution.get()
        self.window.destroy()
        if new_res and new_res != old_res:
            # 分辨率变了 → 自动重启程序生效（2026-08-15）
            if messagebox.askyesno("重启生效", f"分辨率已改为 {new_res}。\n重启程序后生效，是否立即重启？"):
                _restart_app()

    def on_test_connection(self):
        """测试连接（2026-09-17 改）：用界面上当前的厂商/地址/密钥/主力模型实测一次"""
        from ..api_client import call_main
        self._stash_form()
        provider = self._cur_provider
        preset = PROVIDER_PRESETS[provider]
        api_base = self._base_cache.get(provider, "").strip() or preset["api_base"]
        api_key = self._key_cache.get(provider, "").strip()
        model = self._model_cache.get(provider, {}).get("main", "").strip()

        if preset.get("needs_key", True) and not api_key:
            messagebox.showinfo("测试连接", f"请先填写 {preset['label']} 的 API 密钥。", parent=self.window)
            return
        if not api_base:
            messagebox.showinfo("测试连接", "请先填写 API 地址。", parent=self.window)
            return
        if not model:
            messagebox.showinfo("测试连接", "请先填写主力模型名。", parent=self.window)
            return

        self.window.config(cursor="watch")
        self.window.update_idletasks()
        try:
            with self._temp_api_config(provider, model, api_base, api_key or LOCAL_PLACEHOLDER_KEY):
                content, ok = call_main("请回复'连接正常'四个字。", "测试连接", temperature=0.1)
            if ok:
                messagebox.showinfo("测试连接", f"连接成功！响应：{content[:60]}", parent=self.window)
            else:
                messagebox.showerror("测试连接", f"连接失败：{content[:200]}", parent=self.window)
        except Exception as e:
            messagebox.showerror("测试连接", f"连接出错：{e}", parent=self.window)
        finally:
            self.window.config(cursor="")

    def on_cancel(self):
        self.window.destroy()


class MainMenu:
    """主菜单窗口"""

    def __init__(self, root, on_new_game, on_continue_game):
        self.root = root
        self.on_new_game = on_new_game
        self.on_continue_game = on_continue_game

        pad = _s(20)
        self._inner_pad = pad * 2
        self.frame = ttk.Frame(root, padding=pad)
        self.frame.pack(fill=BOTH, expand=YES)

        # 分辨率锁定后窗口固定1920×1080：菜单内容放进居中容器，避免挤在左上
        # 高度随容器收缩：1366×768 上窗口只有 ~689 高，写死 720 会把底部按钮推出窗口下沿
        self.inner = ttk.Frame(self.frame)
        self._inner_w, self._inner_h = _s(900), _s(720)
        self.inner.place(relx=0.5, rely=0.5, anchor="center",
                         width=self._inner_w, height=self._inner_h)
        self.frame.bind("<Configure>", self._fit_inner)

        # 设置按钮放右上角（不再挤底部按钮栏，2026-08-14 用户要求）
        self.settings_btn = ttk.Button(self.frame, text="设置", command=self.open_settings,
                                       bootstyle=SECONDARY, width=_s(8))
        self.settings_btn.place(relx=1.0, x=-8, rely=0.0, y=8, anchor="ne")

        self.build_ui()

    def _fit_inner(self, event):
        """容器比 inner 期望尺寸小时按容器收缩，避免底部按钮栏被挤出窗口下沿"""
        w = min(self._inner_w, event.width - self._inner_pad)
        h = min(self._inner_h, event.height - self._inner_pad)
        if (w, h) != (self.inner.winfo_width(), self.inner.winfo_height()):
            self.inner.place_configure(width=w, height=h)

    def build_ui(self):
        # 标题
        title = ttk.Label(self.inner, text="AI 叙事 RPG", font=(FONT_FAMILY, font_size(20), "bold"))
        title.pack(pady=(_s(20), _s(30)))

        # 存档列表
        ttk.Label(self.inner, text="存档（点击存档行的【继续】即可读取）").pack(anchor=W, pady=(_s(0), _s(10)))

        # 按钮栏（P2：去掉部分Windows字体下显示为方框的emoji，统一纯文字按钮）
        # 先于存档列表 pack 且钉底：空间不足时优先保证「新游戏」「退出」可见
        btn_frame = ttk.Frame(self.inner)
        btn_frame.pack(side=BOTTOM, fill=X, pady=(_s(10), _s(0)))

        self.save_list_frame = ttk.Frame(self.inner)
        self.save_list_frame.pack(fill=BOTH, expand=YES, pady=(_s(0), _s(20)))

        self.refresh_save_list()

        ttk.Button(btn_frame, text="新游戏", command=self.on_new_game, bootstyle=PRIMARY, width=_s(12)).pack(side=LEFT, padx=_s(5))
        ttk.Button(btn_frame, text="退出", command=self.root.quit, bootstyle=DANGER, width=_s(12)).pack(side=RIGHT, padx=_s(5))

    def refresh_save_list(self):
        """刷新存档列表"""
        for widget in self.save_list_frame.winfo_children():
            widget.destroy()

        saves = list_saves()
        for save in saves:
            row = ttk.Frame(self.save_list_frame)
            row.pack(fill=X, pady=_s(2))

            # 存档名称
            name_label = ttk.Label(row, text=save["name"], width=_s(15))
            name_label.pack(side=LEFT)

            if save["exists"]:
                info_text = f"轮次: {save['rounds']} | 最后游玩: {save['last_played']}"
                ttk.Label(row, text=info_text, foreground="gray").pack(side=LEFT, padx=(_s(10), _s(0)))

                ttk.Button(row, text="继续", command=lambda s=save: self.on_continue_game(s["name"]),
                          bootstyle=INFO, width=_s(6)).pack(side=RIGHT, padx=_s(2))
                ttk.Button(row, text="删除", command=lambda s=save: self.on_delete_save(s["name"]),
                          bootstyle=DANGER, width=_s(6)).pack(side=RIGHT, padx=_s(2))
            else:
                ttk.Label(row, text="（空）", foreground="gray").pack(side=LEFT, padx=(_s(10), _s(0)))
                ttk.Button(row, text="新建", command=lambda s=save: self.on_new_game(s["name"]),
                          bootstyle=PRIMARY, width=_s(6)).pack(side=RIGHT, padx=_s(2))

    def open_settings(self):
        SettingsDialog(self.root)

    def on_delete_save(self, save_name):
        if messagebox.askyesno("确认删除", f"确定要删除存档 '{save_name}' 吗？"):
            if delete_save(save_name):
                messagebox.showinfo("删除成功", f"存档 '{save_name}' 已删除")
                self.refresh_save_list()
            else:
                messagebox.showerror("删除失败", "存档不存在")
