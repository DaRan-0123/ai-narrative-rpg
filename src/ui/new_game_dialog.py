"""
新游戏/世界初始化对话框（对话式）
玩家通过与AI向导多轮对话，逐步完善世界设定
"""
import tkinter as tk
from tkinter import messagebox
import tkinter.font as tkfont
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import threading
import json

from . import (FONT_FAMILY, COLOR_BG_TEXT, COLOR_FG_MAIN, COLOR_BG_PANEL,
               COLOR_TAG_INPUT, COLOR_TAG_ASSISTANT, COLOR_TAG_SYSTEM,
               place_window, SCALE, font_size)
from ..game_state import GameState
from ..world_builder import build_world, wizard_turn


def _s(v):
    """尺寸缩放（2026-08-15）：× SCALE，1800×1350 时不变"""
    return int(v * SCALE)


# ===== P9: 世界创建向导（对话式）=====


class NewGameDialog:
    """新游戏创建对话框（对话式）"""

    def __init__(self, parent, save_name, on_game_created):
        self.window = ttk.Toplevel(parent)
        self.window.title("创建新世界 - 与向导对话")
        self.window.transient(parent)
        self.window.grab_set()
        # 相对父窗口居中，尺寸随屏幕自适应且不越界（小屏幕右侧设定区可滚动）
        # 2026-08-15 用户定：窗口加大（高为主要增加，宽稍增）
        place_window(self.window, _s(1200), _s(960), min_w=_s(900), min_h=_s(640), parent=parent)

        self.save_name = save_name
        self.on_game_created = on_game_created

        # 修复：处理中标志，防止连击重复提交/关闭窗口导致后台线程写已销毁UI
        self._busy = False
        self.window.protocol("WM_DELETE_WINDOW", self.on_close_attempt)

        # 对话历史（用于P9）
        self.conversation_history = []
        # 当前世界设定草稿
        self.world_draft = {
            "magic": None, "tech": None, "society": None,
            "economy": None, "order": None, "morality": None,
            "starting_area": None, "narrative_style": None,
            "assistant_tone": None, "perspective": None,
            "difficulty": None, "player_name": None,
            "player_appearance": None, "player_background": None,
            "world_vibe": None
        }

        # 设置全局字体
        self._setup_fonts()

        self.build_ui()

        # 开场白
        self._add_assistant_message(
            "你好，我是你的世界创建向导。让我们一起打造属于你的RPG世界。\n\n"
            "你可以用任何方式开始——描述一个场景、一个角色、一种氛围，或者回答：\n"
            "• 你想体验什么样的世界？（魔法？科技？末日？）\n"
            "• 你的主角是谁？\n"
            "• 你希望这个世界给你什么感觉？"
        )

    def _setup_fonts(self):
        """对话框局部字体对象（2026-08-15 字号随分辨率缩放）"""
        self.title_font = tkfont.Font(family=FONT_FAMILY, size=font_size(15), weight="bold")
        self.label_font = tkfont.Font(family=FONT_FAMILY, size=font_size(12))
        self.small_font = tkfont.Font(family=FONT_FAMILY, size=font_size(11))

    def build_ui(self):
        """构建对话式UI：左对话区 + 右表格区"""
        # 主分割窗
        self.paned = ttk.Panedwindow(self.window, orient=HORIZONTAL)
        self.paned.pack(fill=BOTH, expand=YES, padx=_s(10), pady=_s(10))

        # === 左区：对话区 ===
        left_frame = ttk.Frame(self.paned)
        self.paned.add(left_frame, weight=3)

        # 对话标题
        ttk.Label(left_frame, text="与世界向导对话", font=self.title_font).pack(anchor=W, pady=(_s(5), _s(5)))

        # 对话显示区（配色统一用全局常量，与游戏窗口一致）
        self.chat_text = tk.Text(left_frame, wrap=WORD, state=DISABLED,
                                  font=(FONT_FAMILY, font_size(12)),
                                  bg=COLOR_BG_TEXT, fg=COLOR_FG_MAIN,
                                  padx=_s(12), pady=_s(12),
                                  insertbackground="white")
        self.chat_text.pack(fill=BOTH, expand=YES, padx=_s(5), pady=_s(5))
        self.chat_text.tag_config("user", foreground=COLOR_TAG_INPUT)
        self.chat_text.tag_config("assistant", foreground=COLOR_TAG_ASSISTANT)
        self.chat_text.tag_config("system", foreground=COLOR_TAG_SYSTEM,
                                  font=(FONT_FAMILY, font_size(10)))

        # 输入区
        input_frame = ttk.Frame(left_frame)
        input_frame.pack(fill=X, padx=_s(5), pady=_s(5))

        self.chat_input = ttk.Entry(input_frame, font=self.label_font)
        self.chat_input.pack(side=LEFT, fill=X, expand=YES, padx=(_s(0), _s(5)))
        self.chat_input.bind("<Return>", lambda e: self.on_chat_submit())

        self.send_btn = ttk.Button(input_frame, text="发送", command=self.on_chat_submit,
                  bootstyle=PRIMARY, width=_s(8))
        self.send_btn.pack(side=LEFT)

        # 快捷按钮
        quick_frame = ttk.Frame(left_frame)
        quick_frame.pack(fill=X, padx=_s(5), pady=(_s(0), _s(5)))
        self.quick_done_btn = ttk.Button(quick_frame, text="就这样吧", command=lambda: self._quick_send("就这样吧，开始游戏"),
                  bootstyle=INFO, width=_s(10))
        self.quick_done_btn.pack(side=LEFT, padx=_s(2))
        self.quick_random_btn = ttk.Button(quick_frame, text="随机生成", command=self._random_world,
                  bootstyle=INFO, width=_s(10))
        self.quick_random_btn.pack(side=LEFT, padx=_s(2))

        # === 右区：世界设定表格（Canvas+滚动条，小屏幕15项标签不溢出）===
        right_frame = ttk.Frame(self.paned)
        self.paned.add(right_frame, weight=2)

        ttk.Label(right_frame, text="当前世界设定", font=self.title_font).pack(anchor=W, pady=(_s(5), _s(5)))

        right_canvas = tk.Canvas(right_frame, highlightthickness=0, bg=COLOR_BG_PANEL)
        right_scroll = ttk.Scrollbar(right_frame, orient=VERTICAL, command=right_canvas.yview)
        right_inner = ttk.Frame(right_canvas)

        right_inner.bind("<Configure>",
                         lambda e: right_canvas.configure(scrollregion=right_canvas.bbox("all")))
        self._right_canvas_win = right_canvas.create_window((0, 0), window=right_inner, anchor=NW)
        # 内容宽度跟随画布宽度
        right_canvas.bind("<Configure>",
                          lambda e: right_canvas.itemconfigure(self._right_canvas_win, width=e.width))
        right_canvas.configure(yscrollcommand=right_scroll.set)
        # 鼠标滚轮滚动（Windows）
        right_canvas.bind("<Enter>", lambda e: right_canvas.bind_all("<MouseWheel>",
                          lambda ev: right_canvas.yview_scroll(int(-ev.delta / 120), "units")))
        right_canvas.bind("<Leave>", lambda e: right_canvas.unbind_all("<MouseWheel>"))

        right_scroll.pack(side=RIGHT, fill=Y)
        right_canvas.pack(side=LEFT, fill=BOTH, expand=YES)

        # 表格框架
        self.draft_frame = ttk.Labelframe(right_inner, text="已确定的内容", bootstyle=INFO)
        self.draft_frame.pack(fill=BOTH, expand=YES, padx=_s(5), pady=_s(5))

        self.draft_labels = {}
        draft_items = [
            ("magic", "魔法体系"), ("tech", "科技水平"), ("society", "社会形态"),
            ("economy", "经济状况"), ("order", "社会秩序"), ("morality", "道德氛围"),
            ("starting_area", "初始区域"), ("narrative_style", "叙事风格"),
            ("assistant_tone", "助手语气"), ("perspective", "视角"),
            ("difficulty", "难度"), ("player_name", "主角名字"),
            ("player_appearance", "主角外貌"), ("player_background", "主角背景"),
            ("world_vibe", "世界氛围")
        ]
        for key, label in draft_items:
            row = ttk.Frame(self.draft_frame)
            row.pack(fill=X, padx=_s(5), pady=_s(2))
            ttk.Label(row, text=f"{label}:", width=_s(12), font=self.small_font, anchor=E).pack(side=LEFT)
            lbl = ttk.Label(row, text="未确定", font=self.small_font, foreground="gray")
            lbl.pack(side=LEFT, padx=(_s(5), _s(0)))
            self.draft_labels[key] = lbl

        # 底部按钮（随右区一起滚动，小屏幕也能点到）
        btn_frame = ttk.Frame(right_inner)
        btn_frame.pack(fill=X, padx=_s(5), pady=(_s(10), _s(5)))

        self.status_label = ttk.Label(btn_frame, text="等待输入...", foreground="gray", font=self.small_font)
        self.status_label.pack(side=LEFT)

        self.cancel_btn = ttk.Button(btn_frame, text="取消", command=self.on_close_attempt,
                  bootstyle=SECONDARY, width=_s(10))
        self.cancel_btn.pack(side=RIGHT, padx=_s(5))

    def _set_busy(self, busy):
        """切换忙碌状态：禁用/恢复输入与所有按钮，防止重复提交和误关窗"""
        self._busy = busy
        state = DISABLED if busy else NORMAL
        for widget in (self.chat_input, self.send_btn, self.quick_done_btn,
                       self.quick_random_btn, self.cancel_btn):
            try:
                widget.config(state=state)
            except tk.TclError:
                pass  # 窗口可能已销毁

    def on_close_attempt(self):
        """取消按钮/关窗统一入口：处理中禁止关闭"""
        if self._busy:
            self._add_system_message("正在处理中，请稍候……")
            return
        self.window.destroy()

    def _safe_after(self, ms, callback):
        """窗口销毁后后台线程的after回调会抛TclError，这里统一防护"""
        def _guarded():
            try:
                if self.window.winfo_exists():
                    callback()
            except tk.TclError:
                pass  # 窗口已销毁，静默丢弃
        try:
            self.window.after(ms, _guarded)
        except tk.TclError:
            pass

    def _add_user_message(self, text):
        """添加用户消息到对话区"""
        self.chat_text.config(state=NORMAL)
        self.chat_text.insert(END, f"\n你: {text}\n\n", "user")
        self.chat_text.see(END)
        self.chat_text.config(state=DISABLED)

    def _add_assistant_message(self, text):
        """添加AI消息到对话区"""
        self.chat_text.config(state=NORMAL)
        self.chat_text.insert(END, f"向导: {text}\n\n", "assistant")
        self.chat_text.see(END)
        self.chat_text.config(state=DISABLED)

    def _add_system_message(self, text):
        """添加系统消息"""
        self.chat_text.config(state=NORMAL)
        self.chat_text.insert(END, f"[{text}]\n", "system")
        self.chat_text.see(END)
        self.chat_text.config(state=DISABLED)

    def _quick_send(self, text):
        """快捷发送"""
        self.chat_input.delete(0, END)
        self.chat_input.insert(0, text)
        self.on_chat_submit()

    def _random_world(self):
        """随机生成世界描述并发送"""
        import random
        vibes = [
            "我想玩一个高魔蒸汽朋克世界，主角是一个会魔法的机械师",
            "末日废土，主角是变异人，在废墟里寻找旧时代的科技",
            "赛博朋克+修真，主角是一个用神经接口修炼的黑客",
            "中世纪欧洲风格，主角是一个被通缉的骑士",
            "克苏鲁风格的海边小镇，主角是一个调查员"
        ]
        self._quick_send(random.choice(vibes))

    def _update_draft_display(self):
        """更新世界设定表格显示"""
        for key, lbl in self.draft_labels.items():
            value = self.world_draft.get(key)
            if value:
                lbl.config(text=value, foreground="white")
            else:
                lbl.config(text="未确定", foreground="gray")

    def on_chat_submit(self):
        """玩家提交对话"""
        # 修复：处理中直接忽略，防止连击重复提交
        if self._busy:
            return
        user_input = self.chat_input.get().strip()
        if not user_input:
            return

        self.chat_input.delete(0, END)
        self._add_user_message(user_input)
        self._set_busy(True)
        self.status_label.config(text="向导思考中...", foreground="blue")

        threading.Thread(target=self._process_chat, args=(user_input,), daemon=True).start()

    def _process_chat(self, user_input):
        """后台处理对话（P9 调用在 src/world_builder.py，与服务版共用）"""
        try:
            out = wizard_turn(self.conversation_history, user_input, self.world_draft)
            if "error" in out:
                self._safe_after(0, lambda: self._show_error(out["error"]))
                return

            self.world_draft = out["world_draft"]

            # 记录对话历史
            self.conversation_history.append({"role": "user", "content": user_input})
            self.conversation_history.append(
                {"role": "assistant", "content": json.dumps(out["raw"], ensure_ascii=False)})

            # 限制历史长度（保留最近10轮）
            if len(self.conversation_history) > 20:
                self.conversation_history = self.conversation_history[-20:]

            # UI更新
            self._safe_after(0, lambda: self._update_after_chat(out["reply"], out["is_done"]))

        except Exception as e:
            import traceback
            traceback.print_exc()
            err_msg = f"处理出错: {str(e)}"
            self._safe_after(0, lambda: self._show_error(err_msg))

    def _update_after_chat(self, reply, is_done):
        """对话处理后的UI更新"""
        self._add_assistant_message(reply)
        self._update_draft_display()

        if is_done:
            self.status_label.config(text="世界设定完成！正在生成...", foreground="green")
            self._add_system_message("正在生成完整世界...")
            # 生成期间保持busy（输入和按钮继续禁用，防止重复触发/关窗）
            # 后台生成世界
            threading.Thread(target=self._generate_world, daemon=True).start()
        else:
            self._set_busy(False)
            self.chat_input.focus()
            self.status_label.config(text="继续对话完善世界...", foreground="gray")

    def _generate_world(self):
        """对话结束后，生成完整世界并落盘。
        链路实现（P10→P6→P8）在 src/world_builder.py，与服务版共用同一份"""
        try:
            world_data, player_info, settings, err = build_world(
                self.world_draft,
                on_progress=lambda m: self._safe_after(0, lambda: self._add_system_message(m)))

            if err:
                self._safe_after(0, lambda: self._show_error(err))
                return

            GameState(self.save_name).init_new(world_data, player_info, settings)

            self._safe_after(0, lambda: self._add_system_message("世界创建完成！"))
            self._safe_after(1000, lambda: self._finish())

        except Exception as e:
            import traceback
            traceback.print_exc()
            err_msg = f"生成失败: {str(e)}"
            self._safe_after(0, lambda: self._show_error(err_msg))

    def _show_error(self, message):
        """显示错误"""
        self._add_system_message(f"错误: {message}")
        self.status_label.config(text="出错了，请重试", foreground="red")
        # 修复：出错时恢复全部控件（含取消按钮），让用户可以重试或关窗
        self._set_busy(False)

    def _finish(self):
        """完成创建"""
        self.window.destroy()
        self.on_game_created(self.save_name)
