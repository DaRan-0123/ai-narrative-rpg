"""
调试模式窗口
游戏运行时开启，实时显示所有API发送和接收的内容
"""
import queue
import tkinter as tk
from tkinter import ttk
import json
import datetime

from . import (FONT_FAMILY, COLOR_BG_CODE, COLOR_FG_CODE, place_window, SCALE, font_size)

# API工作线程 -> 主线程 的日志队列（跨线程安全；DebugWindow 用 after 周期 drain 到UI）
_log_queue = queue.Queue()


def _s(v):
    """尺寸缩放（2026-08-15）：× SCALE，1800×1350 时不变"""
    return int(v * SCALE)


class DebugWindow:
    """调试窗口：实时显示API调用详情"""

    _instance = None

    def __new__(cls, *args, **kwargs):
        """单例模式，确保只有一个调试窗口"""
        if cls._instance is None or not cls._instance._is_alive():
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, parent=None):
        if self._initialized:
            self.show()
            return
        self._initialized = True

        self.parent = parent
        self.window = tk.Toplevel(parent) if parent else tk.Tk()
        self.window.title("API Debug Window")
        # 相对父窗口居中，尺寸随屏幕自适应且不越界
        # 2026-08-15 用户定：长宽都改为原来的1.5倍（1000×700 → 1500×1050）；随分辨率缩放
        place_window(self.window, _s(1500), _s(1050), min_w=_s(700), min_h=_s(450), parent=parent)
        self.window.protocol("WM_DELETE_WINDOW", self.hide)

        self._build_ui()
        self._log_counter = 0
        # 主线程周期 drain 日志队列（after 自调度，线程安全，替代工作线程直接 after）
        self._schedule_drain()

    def _schedule_drain(self):
        """每300ms主线程从队列取日志写入UI（工作线程只入队，不碰Tk）"""
        try:
            self._drain_queue()
            self.window.after(300, self._schedule_drain)
        except tk.TclError:
            pass  # 窗口已销毁

    def _drain_queue(self):
        try:
            while True:
                args = _log_queue.get_nowait()
                try:
                    self.log(*args)
                except Exception:
                    pass
        except queue.Empty:
            pass

    def _is_alive(self):
        """检查窗口是否还存在"""
        try:
            return self.window.winfo_exists()
        except:
            return False

    def _build_ui(self):
        """构建调试窗口UI"""
        # 顶部控制栏
        ctrl_frame = ttk.Frame(self.window)
        ctrl_frame.pack(fill=tk.X, padx=_s(10), pady=_s(5))

        ttk.Label(ctrl_frame, text="API Call Log", font=(FONT_FAMILY, font_size(14), "bold")).pack(side=tk.LEFT)

        ttk.Button(ctrl_frame, text="Clear", command=self.clear).pack(side=tk.RIGHT, padx=_s(5))
        ttk.Button(ctrl_frame, text="Export log", command=self.export).pack(side=tk.RIGHT, padx=_s(5))

        # 日志列表（左侧：调用摘要）
        paned = ttk.Panedwindow(self.window, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=tk.YES, padx=_s(10), pady=_s(5))

        # 左侧面板：调用列表
        left_frame = ttk.Frame(paned)
        paned.add(left_frame, weight=1)

        # 列表标题
        columns = ("time", "type", "model", "status")
        self.tree = ttk.Treeview(left_frame, columns=columns, show="headings", height=_s(20))
        self.tree.heading("time", text="Time")
        self.tree.heading("type", text="Type")
        self.tree.heading("model", text="Model")
        self.tree.heading("status", text="Status")
        self.tree.column("time", width=_s(80))
        self.tree.column("type", width=_s(80))
        self.tree.column("model", width=_s(120))
        self.tree.column("status", width=_s(60))
        self.tree.pack(fill=tk.BOTH, expand=tk.YES, side=tk.LEFT)

        scrollbar = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar.pack(fill=tk.Y, side=tk.RIGHT)
        self.tree.configure(yscrollcommand=scrollbar.set)

        # 右侧面板：详情显示
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=2)

        # 详情标签页
        self.detail_notebook = ttk.Notebook(right_frame)
        self.detail_notebook.pack(fill=tk.BOTH, expand=tk.YES)

        # 请求页
        req_frame = ttk.Frame(self.detail_notebook, padding=_s(5))
        self.detail_notebook.add(req_frame, text=" Request ")
        self.req_text = tk.Text(req_frame, wrap=tk.WORD, font=("Consolas", font_size(11)),
                                 bg=COLOR_BG_CODE, fg=COLOR_FG_CODE,
                                 padx=_s(10), pady=_s(10), insertbackground="white")
        self.req_text.pack(fill=tk.BOTH, expand=tk.YES)
        req_scroll = ttk.Scrollbar(req_frame, orient=tk.VERTICAL, command=self.req_text.yview)
        req_scroll.pack(fill=tk.Y, side=tk.RIGHT)
        self.req_text.configure(yscrollcommand=req_scroll.set)

        # 响应页
        resp_frame = ttk.Frame(self.detail_notebook, padding=_s(5))
        self.detail_notebook.add(resp_frame, text=" Response ")
        self.resp_text = tk.Text(resp_frame, wrap=tk.WORD, font=("Consolas", font_size(11)),
                                  bg=COLOR_BG_CODE, fg=COLOR_FG_CODE,
                                  padx=_s(10), pady=_s(10), insertbackground="white")
        self.resp_text.pack(fill=tk.BOTH, expand=tk.YES)
        resp_scroll = ttk.Scrollbar(resp_frame, orient=tk.VERTICAL, command=self.resp_text.yview)
        resp_scroll.pack(fill=tk.Y, side=tk.RIGHT)
        self.resp_text.configure(yscrollcommand=resp_scroll.set)

        # 绑定列表选择事件
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # 存储所有日志数据
        self._logs = {}

    def _on_select(self, event):
        """当选择列表项时显示详情"""
        selection = self.tree.selection()
        if not selection:
            return
        item_id = selection[0]
        log_data = self._logs.get(item_id, {})

        # 显示请求
        self.req_text.delete("1.0", tk.END)
        req = log_data.get("request", {})
        self.req_text.insert(tk.END, self._format_json(req))

        # 显示响应
        self.resp_text.delete("1.0", tk.END)
        resp = log_data.get("response", {})
        self.resp_text.insert(tk.END, self._format_json(resp))

    def _format_json(self, data):
        """格式化JSON数据为可读字符串"""
        if not data:
            return "(No data)"
        try:
            return json.dumps(data, ensure_ascii=False, indent=2)
        except:
            return str(data)

    def log(self, request_data, response_data, model_type="main", success=True):
        """
        记录一次API调用

        参数:
            request_data: 请求数据字典
            response_data: 响应数据字典
            model_type: "main" 或 "lightweight"
            success: 是否成功
        """
        self._log_counter += 1
        log_id = f"log_{self._log_counter}"

        now = datetime.datetime.now().strftime("%H:%M:%S")
        status = "✓" if success else "✗"

        # 提取模型名称
        model = request_data.get("model", "unknown") if isinstance(request_data, dict) else "unknown"

        # 插入到列表
        self.tree.insert("", tk.END, iid=log_id, values=(now, model_type, model, status))
        self.tree.see(log_id)

        # 保存完整数据
        self._logs[log_id] = {
            "request": request_data,
            "response": response_data,
            "timestamp": now,
            "model_type": model_type,
            "success": success
        }

        # 自动选中新条目
        self.tree.selection_set(log_id)
        self._on_select(None)

    def clear(self):
        """清空所有日志"""
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._logs.clear()
        self._log_counter = 0
        self.req_text.delete("1.0", tk.END)
        self.resp_text.delete("1.0", tk.END)

    def export(self):
        """导出日志到文件"""
        from tkinter import filedialog
        filename = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("Text", "*.txt")],
            title="Export API log"
        )
        if not filename:
            return
        try:
            export_data = {
                "exported_at": datetime.datetime.now().isoformat(),
                "total_logs": len(self._logs),
                "logs": self._logs
            }
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            import tkinter.messagebox as msgbox
            msgbox.showerror("Export failed", str(e))

    def show(self):
        """显示窗口"""
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def hide(self):
        """隐藏窗口（不销毁）"""
        self.window.withdraw()

    def destroy(self):
        """销毁窗口"""
        try:
            self.window.destroy()
        except:
            pass
        DebugWindow._instance = None


# 全局调试窗口引用
debug_window = None


def get_debug_window(parent=None):
    """获取或创建调试窗口"""
    global debug_window
    if debug_window is None:
        debug_window = DebugWindow(parent)
    return debug_window


def toggle_debug(parent=None):
    """切换调试窗口显示/隐藏"""
    global debug_window
    if debug_window is None:
        debug_window = DebugWindow(parent)
        debug_window.show()
    else:
        try:
            if debug_window.window.winfo_viewable():
                debug_window.hide()
            else:
                debug_window.show()
        except:
            debug_window = DebugWindow(parent)
            debug_window.show()
    return debug_window


def log_api_call(request_data, response_data, model_type="main", success=True):
    """记录API调用（如果调试窗口存在）。
    工作线程只入队（线程安全），由 DebugWindow 主线程 after 周期 drain 到UI"""
    if debug_window is not None:
        try:
            if debug_window._is_alive():
                _log_queue.put((request_data, response_data, model_type, success))
        except Exception:
            pass
