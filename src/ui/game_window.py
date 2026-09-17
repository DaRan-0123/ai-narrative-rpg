"""
游戏主窗口（表现层）

回合编排逻辑在 src/engine.py 的 GameEngine 里，本文件只负责把它画出来。
三带布局（2026-08-06 重构，窗口锁1920×1080，固定比例place分区；
立绘2026-08-14封存，游戏助手2026-08-14从底部带迁入中间带右侧）：
  顶栏（通栏信息条）
  中间带：主对话框(70%) · 游戏助手(30%)
  底部带：状态卡片4列×2行（通栏）
"""
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import threading
import json
import re

from . import (FONT_FAMILY, COLOR_BG_TEXT, COLOR_BG_TEXT_ALT,
               COLOR_FG_MAIN, COLOR_FG_DIM, COLOR_BG_PANEL,
               COLOR_TAG_INPUT, COLOR_TAG_SYSTEM, COLOR_TAG_ERROR,
               FONT_SIZE_TOP_SCALED, FONT_SIZE_CARD_TITLE_SCALED,
               FONT_SIZE_BODY_SCALED, FONT_SIZE_PANEL_TITLE_SCALED,
               SCALE,
               CARD_BG, CARD_OUTLINE, CARD_TITLE_FG)
from ..engine import GameEngine
from ..vocab import is_alone, is_world_event
from ..game_state import GameState, MEMORY_CONSOLIDATE_THRESHOLD, parse_time_passed
from ..api_client import call_main, call_main_json, call_lightweight, call_main_stream, set_debug_mode, is_debug_mode
from ..prompts import (
    P1_SYSTEM, build_p1_user,
    P2_SYSTEM, build_p2_user,
    P3_SYSTEM, build_p3_user,
    P4_SYSTEM, build_p4_user,
    P4C_SYSTEM, build_p4c_user,
    P5_SYSTEM, build_p5_user,
    P6_SYSTEM, build_p6_user,
    P11_SYSTEM, build_p11_user,
    P12_SYSTEM, build_p12_user,
    P13_SYSTEM, build_p13_user,
    build_p14_prompts, build_p1_judge_user
)


# 叙事风格下拉悬停解释（2026-08-14）：鼠标移到选项上时右侧悬浮显示
STYLE_DESCRIPTIONS = {
    "冷硬写实": "冷硬写实，聚焦泥泞、尘土与真实代价；客观陈述所见，不带抒情",
    "诗意优美": "文笔细腻，多用意象与留白，字里行间有诗意的余味",
    "客观简洁": "白描为主，三言两语交代事实，信息密度高、节奏快",
    "华丽文学": "辞藻铺陈、句式讲究，讲究氛围与画面感的浓墨重彩",
    "意识流": "贴近角色的感官与思绪流转，跳跃联想，主观色彩浓",
}



# ===== 全窗口统一字体梯度（2026-08-06 用户定案；尺寸常量集中在 ui/__init__.py）=====
FONT_TOP = (FONT_FAMILY, FONT_SIZE_TOP_SCALED)                       # 顶栏
FONT_BODY = (FONT_FAMILY, FONT_SIZE_BODY_SCALED)                     # 正文（叙事/对话/输入）
FONT_PANEL_TITLE = (FONT_FAMILY, FONT_SIZE_PANEL_TITLE_SCALED, "bold")  # 面板标题
FONT_CARD_TITLE = (FONT_FAMILY, FONT_SIZE_CARD_TITLE_SCALED)         # 状态卡片分类名
FONT_CARD_VALUE = (FONT_FAMILY, FONT_SIZE_BODY_SCALED)               # 状态卡片值

# 三带高度分配（窗口锁分辨率）：底部带显式钉死，顶栏/中间带吃剩余
# 2026-08-15 随分辨率缩放：216 × SCALE（1800×1350 时=216）
BOTTOM_BAND_H = int(216 * SCALE)   # 底部带96DPI基线高度(px) ≈ 高 × 20%

# 尺寸缩放 helper（2026-08-15）：像素/字符数 × SCALE，1800×1350 时不变
def _s(v):
    return int(v * SCALE)


def measure_bottom_band_h():
    """底部带实际高度：随字体行高（系统DPI缩放）自适应，下限=BOTTOM_BAND_H。
    Tk正数字号=点阵，main.py DPI感知后高缩放屏字体变大（200%会话实测行高17→31px），
    钉死像素会把卡片第二行/助手区裁掉（实测溢出300>216）；按行高比例放大底带。
    须在root存在后调用（build_ui里）。"""
    try:
        linespace = tkfont.Font(family=FONT_FAMILY, size=FONT_SIZE_BODY_SCALED).metrics("linespace")
        return max(BOTTOM_BAND_H, round(BOTTOM_BAND_H * linespace / 17))  # 17=96DPI实测基线
    except (tk.TclError, RuntimeError):   # 无默认root(RuntimeError)/解释器异常(TclError)
        return BOTTOM_BAND_H


class StatusCard(tk.Canvas):
    """深色圆角状态卡片（tkinter/ttk无原生圆角，用Canvas多边形smooth样条画法）。
    内容：分类名（小字灰蓝，顶部）+ 值文本（主色，宽度跟随栏宽自动换行）。
    宽度变化时<Configure>重算折行并重绘；高度按文本行数自适应。
    max_lines：设定后最多显示N行，超出省略号截断，且高度固定为N行高（不足也占满，同行等高）。
    更新接口：set(text)；兼容旧的 .config(text=...) 调用方式（调用点不用改）。"""

    RADIUS = _s(10)  # 圆角半径(px)，随分辨率缩放
    PAD = _s(10)     # 卡片内边距(px)，随分辨率缩放

    def __init__(self, parent, title, initial="—", max_lines=None):
        # width=1：自报极小需求宽度——Canvas默认需求宽约380px，
        # 多卡等分行里会挤占后pack卡片的空间（pack赤字时逆序饿死）
        super().__init__(parent, bg=COLOR_BG_PANEL, highlightthickness=0, bd=0,
                         width=1, height=_s(50))
        self._title = title
        self._text = initial
        self._max_lines = max_lines   # 最多显示行数（None=自适应不限制）
        self._last_w = 0
        self._truncated = False       # 当前是否发生省略号截断（只有截断才显示tooltip）
        self.bind("<Configure>", self._on_configure)

    def set(self, text):
        """更新值文本（空值统一占位"—"，与旧行为一致）"""
        self._text = text or "—"
        self._redraw()

    def get_text(self):
        """完整内容（tooltip 悬浮提示用，不受 max_lines 截断影响）"""
        return self._text

    def is_truncated(self):
        """是否发生省略号截断（只有截断时才显示悬浮提示）"""
        return self._truncated

    def config(self, cnf=None, **kw):
        """兼容接口：拦截 text= 走set()，其余透传Canvas"""
        if "text" in kw:
            self.set(kw.pop("text"))
        if cnf or kw:
            super().config(cnf, **kw)
    configure = config

    def _on_configure(self, event):
        """宽度变化才重绘（高度变化是_redraw自己调的，避免循环）"""
        if event.width > 10 and event.width != self._last_w:
            self._last_w = event.width
            self._redraw()

    def _wrap_lines(self, text, avail_w):
        """按像素宽度手动折行（中文/英文都按字符累积，measure精确换行）。
        返回 (折行后的行列表, 值文本Font)"""
        font = tkfont.Font(self, family=FONT_FAMILY, size=FONT_SIZE_BODY_SCALED)
        lines = []
        cur = ""
        for ch in text:
            if font.measure(cur + ch) <= avail_w:
                cur += ch
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
        return lines, font

    def _redraw(self):
        """全量重绘：先折行（超行截断省略号）→ 定高（固定N行或按内容自适应）→ 画圆角底 → 文本置顶"""
        w = self._last_w
        if w < 10:
            return  # 尚未完成首次布局
        self.delete("all")
        pad = self.PAD
        # 分类名（小字次要色，左上）
        title_item = self.create_text(pad, pad, anchor="nw", text=self._title,
                                      font=FONT_CARD_TITLE, fill=CARD_TITLE_FG)
        tb = self.bbox(title_item) or (pad, pad, pad, pad + 14)
        # 值文本：手动折行；超过 max_lines 截断、最后一行加省略号
        value_y = tb[3] + 4
        avail_w = max(1, w - 2 * pad)
        lines, font = self._wrap_lines(self._text, avail_w)
        max_lines = self._max_lines
        if max_lines is not None and len(lines) > max_lines:
            kept = lines[:max_lines - 1]          # 前 N-1 行完整
            last = lines[max_lines - 1]           # 第 N 行截断 + 省略号
            ell = "…"
            ell_w = font.measure(ell)
            s = ""
            for ch in last:
                if font.measure(s + ch) + ell_w <= avail_w:
                    s += ch
                else:
                    break
            kept.append(s + ell)
            lines = kept
        value_item = self.create_text(pad, value_y, anchor="nw",
                                      text="\n".join(lines),
                                      font=FONT_CARD_VALUE, fill=COLOR_FG_MAIN)
        self._truncated = (max_lines is not None and len(self._wrap_lines(self._text, avail_w)[0]) > max_lines)
        if max_lines is not None:
            # 固定 N 行高（不足也占满，同一行的卡片高度一致）
            line_h = font.metrics("linespace")
            card_h = pad + (tb[3] - tb[1]) + 4 + max_lines * line_h + pad
        else:
            vb = self.bbox(value_item) or (pad, value_y, pad, value_y + 18)
            card_h = vb[3] + pad
        # 圆角矩形底（经典8点smooth样条画法），文本置顶
        self._draw_round_rect(1, 1, w - 2, max(card_h - 2, 20))
        self.tag_raise(title_item)
        self.tag_raise(value_item)
        # 高度按文本行数自适应（直接调基类config，绕开text拦截）
        tk.Canvas.config(self, height=card_h)

    def _draw_round_rect(self, x1, y1, x2, y2):
        """圆角矩形：每角3个控制点，smooth样条自然成圆角"""
        r = self.RADIUS
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,   # 上边 → 右上角
            x2, y2 - r, x2, y2, x2 - r, y2,               # 右边 → 右下角
            x1 + r, y2, x1, y2, x1, y2 - r,               # 下边 → 左下角
            x1, y1 + r, x1, y1, x1 + r, y1,               # 左边 → 左上角
        ]
        return self.create_polygon(points, smooth=True,
                                   fill=CARD_BG, outline=CARD_OUTLINE, width=1)


class ToolTip:
    """悬浮提示：鼠标停在卡片上时，在卡片正上方弹出小窗显示完整内容。
    只对发生省略号截断的卡片显示（get_text 提供全文）。
    定位：以卡片屏幕坐标为准，弹窗水平居中于卡片上方、距上沿小间隙；
    屏幕顶部放不下时改显示在卡片下方。深色底浅色字，与 darkly 主题协调"""

    def __init__(self, widget, get_text, is_truncated=None, wraplength=_s(420)):
        self.widget = widget
        self.get_text = get_text
        self.is_truncated = is_truncated or (lambda: True)   # 默认总是显示，可传卡片.is_truncated
        self.wraplength = wraplength
        self._tip = None
        widget.bind("<Enter>", self._on_enter)
        widget.bind("<Leave>", self._on_leave)

    def _on_enter(self, event):
        if self._tip is not None:   # 已显示则不重复建
            return
        if not self.is_truncated():
            return                  # 未截断的卡片不弹悬浮窗
        text = self.get_text()
        if not text:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.attributes("-topmost", True)
        # 深色底浅色字（避免部分主题/截图中 tooltip 反色成黑字黑底）
        ttk.Label(tip, text=text, justify=tk.LEFT, anchor=tk.W,
                  background="#2b2f36", foreground="#e0e0e0",
                  borderwidth=1, relief="solid", wraplength=self.wraplength,
                  padding=(_s(8), _s(6))).pack()
        # 定位：卡片正上方水平居中
        try:
            tip.update_idletasks()
            rw, rh = tip.winfo_reqwidth(), tip.winfo_reqheight()
            wx = self.widget.winfo_rootx()
            wy = self.widget.winfo_rooty()
            ww = self.widget.winfo_width()
            x = wx + (ww - rw) // 2            # 卡片上水平居中
            y = wy - rh - 6                     # 卡片上沿之上 6px
            sw, sh = tip.winfo_screenwidth(), tip.winfo_screenheight()
            if x < 0:
                x = 0
            elif x + rw > sw:
                x = sw - rw
            if y < 0:                           # 顶部放不下 → 显示在卡片下方
                y = wy + self.widget.winfo_height() + 6
            if y + rh > sh:
                y = max(0, sh - rh)
        except Exception:
            x, y = event.x_root + 12, event.y_root + 12
        tip.wm_geometry(f"+{x}+{y}")
        self._tip = tip

    def _on_leave(self, event):
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


class GameWindow(GameEngine):
    """游戏主窗口"""

    def __init__(self, root, save_name, on_return_menu):
        self.root = root
        self.on_return_menu = on_return_menu

        # 引擎标志位 + GameState 由基类初始化（见 src/engine.py）
        super().__init__(save_name)

        # 构建UI
        self.frame = ttk.Frame(root)
        self.frame.pack(fill=BOTH, expand=YES)

        self.build_ui()

        # 显示初始叙事
        self.show_initial_narrative()

        # 初始化状态栏
        self._update_status_bar()

        # 启动叙事风格下拉悬停轮询（常驻，listbox 不可见时零开销空转）
        self._update_style_hover()
    def build_ui(self):
        """三带布局：顶栏通栏 / 中间带(主对话70%·游戏助手30%) / 底部带(状态卡片通栏)"""
        # 顶部信息栏
        self.info_bar = ttk.Frame(self.frame)
        self.info_bar.pack(fill=X, padx=_s(5), pady=_s(5))

        # === 顶栏防溢出（1920×1080锁定布局修复）===
        # pack空间分配是"先pack先得"：右侧按钮先pack(side=RIGHT)钉住位置，
        # 左侧信息文本后pack，空间不足时被压缩/截断的是文字，按钮永远不消失
        # 回退按钮放最外沿（最右），其次返回主菜单（2026-08-14 回退功能）
        self.rollback_btn = ttk.Button(self.info_bar, text="回退", command=self.on_rollback,
                                       bootstyle="danger-outline", width=_s(6))
        self.rollback_btn.pack(side=RIGHT, padx=_s(5))

        self.return_menu_btn = ttk.Button(self.info_bar, text="返回主菜单", command=self.on_return_menu_click,
                                          bootstyle=SECONDARY, width=_s(12))
        self.return_menu_btn.pack(side=RIGHT, padx=_s(5))

        # 手动保存（2026-08-14：选择存档位覆盖保存；位于调试模式与返回主菜单之间）
        self.save_btn = ttk.Button(self.info_bar, text="手动保存", command=self.on_manual_save,
                                   bootstyle=INFO, width=_s(8))
        self.save_btn.pack(side=RIGHT, padx=_s(5))

        # 世界档案（2026-08-15：世界观/剧情线/NPC关系/世界大事记只读展示）
        self.world_btn = ttk.Button(self.info_bar, text="世界", command=self.on_open_world_doc,
                                    bootstyle=INFO, width=_s(5))
        self.world_btn.pack(side=RIGHT, padx=_s(5))

        # 叙事日志（2026-08-15：历史轮次只读回看）
        self.history_btn = ttk.Button(self.info_bar, text="日志", command=self.on_open_history,
                                      bootstyle=INFO, width=_s(5))
        self.history_btn.pack(side=RIGHT, padx=_s(5))

        # 调试模式开关
        self.debug_var = tk.BooleanVar(value=is_debug_mode())
        self.debug_btn = ttk.Checkbutton(
            self.info_bar, text="调试模式",
            variable=self.debug_var,
            command=self.on_debug_toggle,
            bootstyle=("info", "toolbutton")
        )
        self.debug_btn.pack(side=RIGHT, padx=_s(5))

        self.round_label = ttk.Label(self.info_bar, text=f"轮次: {self.game.current_round}",
                                     font=FONT_TOP)
        self.round_label.pack(side=LEFT, padx=_s(5))

        # 游戏内日期·季节（日历系统已有数据，顶栏只读展示）
        self.date_label = ttk.Label(self.info_bar, text="", font=FONT_TOP)
        self.date_label.pack(side=LEFT, padx=_s(20))

        # 叙事风格实时切换
        style_frame = ttk.Frame(self.info_bar)
        style_frame.pack(side=LEFT, padx=_s(20))
        ttk.Label(style_frame, text="叙事风格:", font=FONT_TOP).pack(side=LEFT)
        self.style_var = tk.StringVar(value=self.game.settings.get("narrative_style", "冷硬写实"))
        self.style_combo = ttk.Combobox(style_frame, textvariable=self.style_var,
                                         values=["冷硬写实", "诗意优美", "客观简洁", "华丽文学", "意识流"],
                                         width=_s(12), state="readonly")
        self.style_combo.pack(side=LEFT, padx=(_s(5), _s(0)))
        self.style_combo.bind("<<ComboboxSelected>>", self.on_style_changed)
        # 滚轮悬停不改选中项（与设置对话框一致，2026-08-14）
        self.style_combo.bind("<MouseWheel>", lambda e: "break")
        self.style_combo.bind("<Button-4>", lambda e: "break")
        self.style_combo.bind("<Button-5>", lambda e: "break")
        # 下拉悬停选项显示解释（2026-08-14）：常驻轮询跟踪下拉鼠标行，右侧悬浮显示描述。
        # 注：<<ComboboxPopdown>>/event_generate 事件实测不可靠，故窗口存活期间常驻轮询，
        # listbox 不可见时零开销空转（80ms 检查一次）。关闭下拉/失焦时销毁悬浮窗。
        self._style_hover_tip = None
        self._style_hover_after = None
        self._style_hover_stop = False
        self.style_combo.bind("<<ComboboxSelected>>", self._on_style_popdown_close)
        self.style_combo.bind("<FocusOut>", self._on_style_popdown_close)
        self.style_combo.bind("<Return>", self._on_style_popdown_close)
        self.style_combo.bind("<Escape>", self._on_style_popdown_close)

        # === 底部带（先pack(side=BOTTOM)预留空间）：状态卡片占满（2026-08-14 游戏助手已迁中间带右侧）===
        # 窗口已锁1920×1080，弃用PanedWindow拖拽分栏，全部place固定比例分区。
        # 高度显式钉死（measure_bottom_band_h：96DPI基线216px≈20%，高缩放屏随行高放大）：
        # place奴隶(relheight=1.0)不会向master反推需求高度，靠内容自然撑高实测塌缩成1px
        # （2026-08-06上机实测底部带不可见的病根）
        bottom_band = ttk.Frame(self.frame, height=measure_bottom_band_h())
        bottom_band.pack_propagate(False)
        bottom_band.pack(side=BOTTOM, fill=X, padx=_s(5), pady=(_s(0), _s(5)))

        # --- 状态区（通栏）：8张StatusCard 4列×2行等大网格 ---
        status_area = ttk.Frame(bottom_band)
        status_area.place(relx=0, rely=0, relwidth=1.0, relheight=1.0)

        self.status_frame = ttk.Labelframe(status_area, text="状态", bootstyle=INFO)
        self.status_frame.pack(fill=BOTH, expand=YES, padx=_s(6), pady=(_s(5), _s(0)))

        self.status_labels = {}
        status_items = [
            ("season", "季节"),
            ("time", "时间"),
            ("weather", "天气"),
            ("location", "位置"),
            ("health", "健康"),
            ("items", "物品"),
            ("appearance", "外貌"),
            ("people", "附近NPC"),
        ]
        for idx, (key, label) in enumerate(status_items):
            r, c = divmod(idx, 4)
            # 第1行（下四个）统一固定4行高、超行省略号截断（2026-08-14 用户定）；第0行自适应
            max_lines = 4 if r == 1 else None
            card = StatusCard(self.status_frame, title=label, max_lines=max_lines)
            # sticky="new"：横向拉伸 + 垂直贴顶（同一行卡片高度不同时矮卡顶部对齐、不居中）
            card.grid(row=r, column=c, sticky="new", padx=_s(6), pady=_s(4))
            self.status_frame.columnconfigure(c, weight=1, uniform="status_card")
            self.status_labels[key] = card
            # 悬浮显示完整内容：仅截断的卡才弹（is_truncated），tooltip 用 get_text 取全文
            ToolTip(card, get_text=card.get_text, is_truncated=card.is_truncated)

        # === 中间带（占满剩余高度）：主对话框70% + 游戏助手30% ===
        middle_band = ttk.Frame(self.frame)
        middle_band.pack(fill=BOTH, expand=YES, padx=_s(5), pady=(_s(0), _s(5)))

        # --- 主对话框（左，70%）：叙事区+输入框+风险确认条 ---
        narr_col = ttk.Frame(middle_band)
        narr_col.place(relx=0, rely=0, relwidth=0.70, relheight=1.0)

        # 叙事文本（带滚动条；tag样式统一在此处定义，见append_narrative的tag参数）
        narr_wrap = ttk.Frame(narr_col)
        narr_wrap.pack(fill=BOTH, expand=YES, padx=_s(4), pady=(_s(5), _s(0)))

        self.narrative_text = tk.Text(narr_wrap, wrap=WORD, state=DISABLED,
                                       font=FONT_BODY,
                                       bg=COLOR_BG_TEXT, fg=COLOR_FG_MAIN,
                                       padx=_s(15), pady=_s(15),
                                       insertbackground="white")
        self.narrative_text.pack(side=LEFT, fill=BOTH, expand=YES)
        narr_scroll = ttk.Scrollbar(narr_wrap, orient=VERTICAL, command=self.narrative_text.yview)
        narr_scroll.pack(side=RIGHT, fill=Y)
        self.narrative_text.configure(yscrollcommand=narr_scroll.set)

        # tag样式：玩家输入/系统提示/错误区分显示（字号与正文一致避免行高跳动）
        self.narrative_text.tag_config("input", foreground=COLOR_TAG_INPUT)
        self.narrative_text.tag_config("system", foreground=COLOR_TAG_SYSTEM,
                                       font=FONT_BODY)
        self.narrative_text.tag_config("error", foreground=COLOR_TAG_ERROR,
                                       font=(FONT_FAMILY, FONT_SIZE_BODY_SCALED, "bold"))

        # 叙事输入
        left_input_frame = ttk.Frame(narr_col)
        left_input_frame.pack(fill=X, padx=_s(4), pady=_s(5))
        self.left_input_frame = left_input_frame  # 确认条pack(before=...)要用

        self.left_input = ttk.Entry(left_input_frame)
        self.left_input.pack(side=LEFT, fill=X, expand=YES, padx=(_s(0), _s(5)))
        self.left_input.bind("<Return>", lambda e: self.on_left_submit())

        self.left_send_btn = ttk.Button(left_input_frame, text="发送", command=self.on_left_submit,
                                        bootstyle=PRIMARY, width=_s(8))
        self.left_send_btn.pack(side=LEFT)

        # P13风险确认条（裁定链路，默认隐藏；P13判定有风险时显示在输入框上方）
        self.risk_frame = ttk.Frame(narr_col)
        self.risk_label = ttk.Label(self.risk_frame, text="", bootstyle="danger",
                                    font=FONT_BODY)
        self.risk_label.pack(side=LEFT, padx=_s(5), fill=X, expand=YES)
        self.risk_cancel_btn = ttk.Button(self.risk_frame, text="换个做法",
                                          command=self.on_risk_cancel,
                                          bootstyle=SECONDARY, width=_s(10))
        self.risk_cancel_btn.pack(side=RIGHT, padx=_s(2))
        self.risk_go_btn = ttk.Button(self.risk_frame, text="放手一搏",
                                      command=self.on_risk_go,
                                      bootstyle=DANGER, width=_s(10))
        self.risk_go_btn.pack(side=RIGHT, padx=_s(2))

        # --- 游戏助手（右，30%，2026-08-14 从底部带迁入）：标题+对话区+查询输入 ---
        assistant_col = ttk.Frame(middle_band)
        assistant_col.place(relx=0.70, rely=0, relwidth=0.30, relheight=1.0)

        ttk.Label(assistant_col, text="游戏助手", font=FONT_PANEL_TITLE).pack(anchor=W, padx=(_s(8), _s(5)), pady=(_s(5), _s(0)))

        # 系统对话区（带滚动条；height=9初始请求高，中间带高度下由expand撑满整列）
        sys_wrap = ttk.Frame(assistant_col)
        sys_wrap.pack(fill=BOTH, expand=YES, padx=(_s(8), _s(5)), pady=_s(5))

        self.system_text = tk.Text(sys_wrap, wrap=WORD, state=DISABLED,
                                    font=FONT_BODY, height=9, width=1,
                                    bg=COLOR_BG_TEXT_ALT, fg=COLOR_FG_DIM,
                                    padx=_s(10), pady=_s(10),
                                    insertbackground="white")
        self.system_text.pack(side=LEFT, fill=BOTH, expand=YES)
        sys_scroll = ttk.Scrollbar(sys_wrap, orient=VERTICAL, command=self.system_text.yview)
        sys_scroll.pack(side=RIGHT, fill=Y)
        self.system_text.configure(yscrollcommand=sys_scroll.set)

        # 助手对话tag：玩家提问与助手回复区分（与new_game_dialog统一配色）
        self.system_text.tag_config("user", foreground=COLOR_TAG_INPUT)
        self.system_text.tag_config("assistant", foreground=COLOR_FG_DIM)
        self.system_text.tag_config("system", foreground=COLOR_TAG_SYSTEM)

        # 查询输入
        right_input_frame = ttk.Frame(assistant_col)
        right_input_frame.pack(fill=X, padx=(_s(8), _s(5)), pady=(_s(0), _s(5)))

        self.right_input = ttk.Entry(right_input_frame)
        self.right_input.pack(side=LEFT, fill=X, expand=YES, padx=(_s(0), _s(5)))
        self.right_input.bind("<Return>", lambda e: self.on_right_submit())

        self.right_query_btn = ttk.Button(right_input_frame, text="查询", command=self.on_right_submit,
                                          bootstyle=INFO, width=_s(8))
        self.right_query_btn.pack(side=LEFT)

    # ===== 线程/生命周期防护 =====

    def _safe_after(self, ms, callback):
        """统一收口的after调度：回调执行前检查根窗口与主框架存活，
        防止回合处理中返回主菜单后回调操作已销毁控件（TclError）"""
        def _guarded():
            try:
                if not self.root.winfo_exists():
                    return
                if getattr(self, "frame", None) is not None and not self.frame.winfo_exists():
                    return
                callback()
            except tk.TclError:
                pass  # 控件在回调排队期间被销毁，静默丢弃
        try:
            self.root.after(ms, _guarded)
        except tk.TclError:
            pass  # 根窗口已销毁

    def _set_left_busy(self, busy):
        """回合处理中/结束 的入口可用性切换（防双重提交、防处理中离开）"""
        self._processing_left = busy
        state = DISABLED if busy else NORMAL
        self.left_input.config(state=state)
        self.left_send_btn.config(state=state)
        self.return_menu_btn.config(state=state)
        # 回退按钮：处理中不可回退（回合输出全部完成才可）
        if hasattr(self, "rollback_btn"):
            self.rollback_btn.config(state=state)

    def _set_right_busy(self, busy):
        """查询处理中/结束 的入口可用性切换（防双重提交）"""
        self._processing_right = busy
        state = DISABLED if busy else NORMAL
        self.right_input.config(state=state)
        self.right_query_btn.config(state=state)


    def _notify_background_issue(self, message):
        """后台任务失败提示（系统助手栏一行，不中断游戏）"""
        try:
            self.append_system(f"[{message}]")
        except tk.TclError:
            pass

    def notify_callback_error(self, message):
        """供全局Tk异常钩子调用：在叙事区显示一行错误"""
        try:
            self.append_narrative(f"【错误】{message}", tag="error")
        except tk.TclError:
            pass

    def append_narrative(self, text, tag="normal"):
        """追加叙事文本到主对话框"""
        self.narrative_text.config(state=NORMAL)
        self.narrative_text.insert(END, text + "\n\n", tag)
        self.narrative_text.see(END)
        self.narrative_text.config(state=DISABLED)

    def _remove_generating_placeholder(self):
        """删除叙事区残留的"[正在生成叙事...]"占位行（search 可靠定位，不依赖行号猜测）。
        调用方须已置 narrative_text 为 NORMAL"""
        try:
            idx = self.narrative_text.search("[正在生成叙事...]", "1.0", stopindex=END)
            if idx:
                lineno = int(idx.split(".")[0])
                self.narrative_text.delete(f"{lineno}.0", f"{lineno + 1}.0")
        except tk.TclError:
            pass  # 窗口已销毁

    def _append_streamed_narrative(self, text):
        """P1流式叙事增量实时追加（UI线程，2026-08-14）。
        首块到达时先删除"[正在生成叙事...]"占位行，再逐字插入"""
        try:
            self.narrative_text.config(state=NORMAL)
            if not self._stream_first_shown:
                self._stream_first_shown = True
                self._remove_generating_placeholder()
            self.narrative_text.insert(END, text)
            self.narrative_text.see(END)
            self.narrative_text.config(state=DISABLED)
        except tk.TclError:
            pass  # 窗口已销毁

    def append_system(self, text, is_player=False):
        """追加文本到系统助手栏（tag区分玩家提问/助手回复/系统通知）"""
        self.system_text.config(state=NORMAL)
        if is_player:
            prefix, tag = "你: ", "user"
        elif text.startswith("[") and text.endswith("]"):
            prefix, tag = "", "system"
        else:
            prefix, tag = "助手: ", "assistant"
        self.system_text.insert(END, prefix + text + "\n\n", tag)
        self.system_text.see(END)
        self.system_text.config(state=DISABLED)

    def show_initial_narrative(self):
        """显示开局简介：主角+世界观+位置+叙事"""
        # === 主角简介 ===
        profile = self.game.player_profile
        name = profile.get("name", "无名者")
        appearance = profile.get("appearance", "")
        background = profile.get("background", "")
        
        self.append_narrative("═" * 40)
        self.append_narrative("【你是谁】")
        self.append_narrative(f"名字：{name}")
        if appearance:
            self.append_narrative(f"外貌：{appearance}")
        if background:
            self.append_narrative(f"背景：{background}")
        self.append_narrative("═" * 40)
        
        # === 世界观简介 ===
        world = self.game.world_template
        world_desc = world.get("world_description", "")
        social_fw = world.get("social_framework", "")
        area_desc = world.get("starting_area_description", "")
        
        self.append_narrative("【世界概况】")
        if world_desc:
            self.append_narrative(world_desc[:400])
        if social_fw:
            self.append_narrative(f"社会形态：{social_fw[:300]}")
        self.append_narrative("═" * 40)
        
        # === 当前位置 ===
        loc = self.game.player_state.get("current_location", "未知")

        self.append_narrative("【你在哪】")
        if area_desc:
            self.append_narrative(area_desc[:400])
        self.append_narrative(f"当前位置：{loc}")
        
        # 在场NPC
        scene_people = self.game.player_state.get("current_scene_people", "")
        if not is_alone(scene_people):
            self.append_narrative(f"在场人物：{scene_people}")
        self.append_narrative("═" * 40)
        
        # === 初始叙事 ===
        init_narrative = world.get("initial_situation", "")
        if init_narrative:
            self.append_narrative("【故事开始】")
            self.append_narrative(init_narrative)
            self.append_narrative("═" * 40)
        
        # 如果有历史记录（加载存档），显示最近3轮
        if self.game.current_round > 0:
            self._show_last_rounds(3)

    def _show_last_rounds(self, n=3):
        """显示最近n轮对话历史"""
        history = self.game.action_history
        last_n = history[-n:] if len(history) >= n else history
        if not last_n:
            return
        self.append_narrative("【接上次的冒险】")
        for entry in last_n:
            r = entry.get("round", "?")
            inp = entry.get("input", "")
            narr = entry.get("narrative", "")
            self.append_narrative(f"\n--- 第{r}轮 ---", tag="system")
            self.append_narrative(f"> {inp}", tag="input")
            if narr:
                self.append_narrative(narr)
        self.append_narrative("═" * 40)

    def _update_status_bar(self):
        """根据当前player_state更新顶栏（日期/天气）与中栏状态栏"""
        ps = self.game.player_state

        # 顶栏：游戏内日期·季节（只读展示，数据来自日历系统）
        self.date_label.config(text=self.game.get_date_display())

        # 中栏状态栏
        self.status_labels["season"].config(text=ps.get("game_season", "—") or "—")
        self.status_labels["time"].config(text=ps.get("game_time", "—") or "—")
        self.status_labels["weather"].config(text=ps.get("weather_environment", "—") or "—")
        self.status_labels["location"].config(text=ps.get("current_location", "—") or "—")
        self.status_labels["health"].config(text=ps.get("physical_health", "—") or "—")
        self.status_labels["items"].config(text=ps.get("clothing_equipment", "—") or "—")
        self.status_labels["appearance"].config(text=ps.get("appearance", "—") or "—")
        self.status_labels["people"].config(text=ps.get("current_scene_people", "—") or "—")

    def on_left_submit(self):
        """主对话输入提交：主叙事循环"""
        # 修复：处理中直接忽略，防止快速连击导致重复提交
        if self._processing_left:
            return
        user_input = self.left_input.get().strip()
        if not user_input:
            return

        # 连续风险动作防护：确认条显示期间提交新输入，隐藏旧确认条、按新输入重新走流程
        self._hide_risk_confirm()

        self.left_input.delete(0, END)
        # 记录本回合叙事区起始位置（回退按钮撤销本回合时删除到此处）；
        # 清空旧快照——快照只在真正进入回合（P1前）时才重新保存，风险打回时无快照可回退
        self._round_mark = self.narrative_text.index("end-1c")
        self.game.clear_rollback_snapshot()
        self.append_narrative(f"> {user_input}", tag="input")

        self._set_left_busy(True)
        self.append_narrative("[正在生成叙事...]", tag="system")

        threading.Thread(target=self._process_left_input, args=(user_input,), daemon=True).start()




    # ===== P13风险门 + P14双辩护人 + 裁判P1（2026-08-05 用户定案裁定链路）=====


    def _show_risk_confirm(self, user_input, reason):
        """P13判定有风险（UI线程）：叙事区提示 + 显示确认条；解除busy让玩家可改输入"""
        # 清掉"正在生成叙事..."占位（本线程不会走完正常链路，无人替它清）
        self.narrative_text.config(state=NORMAL)
        lines = self.narrative_text.get("1.0", END).split("\n")
        if lines and "[正在生成叙事...]" in lines[-2]:
            self.narrative_text.delete("end-2l", END)
        self.narrative_text.config(state=DISABLED)

        self.append_narrative(
            f"⚠ 你正在进行一个有风险的尝试" + (f"（{reason}）" if reason else ""),
            tag="error")
        # 确认条：动作摘要（截断防爆版）+ 两个抉择按钮
        action_brief = user_input if len(user_input) <= 30 else user_input[:30] + "…"
        self.risk_label.config(text=f"「{action_brief}」要冒险试试吗？")
        self._risk_action = user_input
        if not self.risk_frame.winfo_ismapped():
            self.risk_frame.pack(fill=X, padx=_s(5), pady=(_s(0), _s(5)),
                                 before=self.left_input_frame)
        self._set_left_busy(False)  # 打回期间解锁：玩家可修改输入换个做法
        self.left_input.focus()

    def _hide_risk_confirm(self):
        """隐藏确认条并清空待确认动作（UI线程调用）"""
        self._risk_action = None
        try:
            if self.risk_frame.winfo_ismapped():
                self.risk_frame.pack_forget()
        except tk.TclError:
            pass

    def on_risk_cancel(self):
        """「换个做法」：本轮作废——不做任何API调用、不写历史，焦点回输入框"""
        self._hide_risk_confirm()
        self.left_input.focus()

    def on_risk_go(self):
        """「放手一搏」：进入裁定链路（P14双辩护人 → 裁判P1）"""
        action = self._risk_action
        if not action:
            return
        self._hide_risk_confirm()
        self.append_narrative("[裁定中：双方分析员调查中...]", tag="system")
        self._set_left_busy(True)  # 重新busy，裁定期间禁止重复提交
        threading.Thread(target=self._process_risky_input, args=(action,),
                         daemon=True).start()





    # ===== P5 世界状态变化（2026-08-15 集成：P1的world_event非空时后台触发，零延迟）=====


    # ===== known_facts 压缩归档（2026-08-15：超长时把旧低置信事实归档成概述，控 token 成本）=====
    # 铁律：事实可以概括整理，但绝对不能少、不能丢——压缩的旧事实移入存档 archives/facts_archive.json
    # 完整保留（可查可恢复），只是不再注入 P1 活跃集；世界观/世界事件/high置信永不归档。


    # ===== NPC 情景记忆：写入与巩固 =====





    # ===== 剧情线：P11故事师回顾 =====


    # ===== P12 地图师：网格坐标定位（2026-08-14地图半封存：图像已封存，位置判断保留供P1空间锚点）=====





    def _p12_report_error(self, message):
        """P12校验/解析失败：记录日志并提示用户（定案2：不自动重试，让用户第一时间看到）"""
        print(f"[地图] {message}")
        self._safe_after(0, lambda m=message: self._notify_background_issue(m))


    def _update_after_left(self, narrative, player_state, skip_narrative=False):
        """主对话处理完成后的UI更新。
        skip_narrative=True：P1流式时叙事已实时显示，跳过重复 append（仍删占位行兜底）"""
        self.narrative_text.config(state=NORMAL)
        self._remove_generating_placeholder()
        self.narrative_text.config(state=DISABLED)

        if not skip_narrative:
            self.append_narrative(narrative)

        self.round_label.config(text=f"轮次: {self.game.current_round}")

        # 更新状态栏（位置/天气在状态栏卡片展示）
        self._update_status_bar()

        self._set_left_busy(False)
        self.left_input.focus()

    def _show_error(self, message):
        """显示错误信息"""
        self.narrative_text.config(state=NORMAL)
        self._remove_generating_placeholder()
        self.narrative_text.config(state=DISABLED)

        self.append_narrative(f"【错误】{message}", tag="error")
        self._set_left_busy(False)

    def on_right_submit(self):
        """游戏助手输入提交：系统查询"""
        # 修复：查询处理中直接忽略，防止重复提交
        if self._processing_right:
            return
        query = self.right_input.get().strip()
        if not query:
            return

        self.right_input.delete(0, END)
        self.append_system(query, is_player=True)
        self.append_system("[正在检索...]")
        self._set_right_busy(True)

        threading.Thread(target=self._process_right_input, args=(query,), daemon=True).start()

    def on_style_changed(self, event=None):
        """叙事风格切换"""
        new_style = self.style_var.get()
        self.game.settings["narrative_style"] = new_style
        self.game.save_manager.save_settings(self.game.settings)
        self.append_system(f"叙事风格已切换为：{new_style}")

    # ===== 叙事风格下拉悬停解释（2026-08-14）：选项上悬浮显示描述 =====

    def _on_style_popdown(self, event=None):
        """点击下拉：启动轮询跟踪鼠标所在选项（保留接口，常驻轮询下通常无需显式调用）"""
        self._style_hover_stop = False
        if self._style_hover_after is None:
            self._update_style_hover()

    def _on_style_popdown_close(self, event=None):
        """下拉关闭/失焦：销毁悬浮窗（常驻轮询继续空转检查，不停止）"""
        self._destroy_style_hover_tip()

    def _destroy_style_hover_tip(self):
        if self._style_hover_tip is not None:
            try:
                self._style_hover_tip.destroy()
            except tk.TclError:
                pass
            self._style_hover_tip = None

    def _update_style_hover(self):
        """轮询下拉 listbox 的鼠标所在行，右侧悬浮显示该选项解释。
        下拉关闭（listbox 不可见）时销毁悬浮窗并停止轮询"""
        try:
            if not self.style_combo.winfo_exists():
                return
            # 取下拉 listbox：Combobox 内部路径 <combobox>.popdown.f.l。
            # nametowidget 对 popdown 子窗口抛 KeyError，故全程用 tk call 直接操作。
            lb_path = str(self.style_combo) + ".popdown.f.l"
            try:
                if str(self.root.tk.call("winfo", "class", lb_path)) != "Listbox":
                    return  # 下拉未打开
                # 下拉未打开/已关闭（不可见）→ 销毁悬浮窗，空转（finally 继续 after，常驻）
                if not int(self.root.tk.call("winfo", "viewable", lb_path)):
                    self._destroy_style_hover_tip()
                    return
            except tk.TclError:
                return
            # 鼠标在 listbox 上的相对坐标 → 命中行
            hover_index = None
            try:
                mx = self.style_combo.winfo_pointerx() - int(self.root.tk.call("winfo", "rootx", lb_path))
                my = self.style_combo.winfo_pointery() - int(self.root.tk.call("winfo", "rooty", lb_path))
                idx = int(self.root.tk.call(lb_path, "index", "@%d,%d" % (mx, my)))
                hover_index = idx
            except (tk.TclError, ValueError):
                hover_index = None
            values = list(self.style_combo.cget("values"))
            desc = None
            if hover_index is not None and 0 <= hover_index < len(values):
                desc = STYLE_DESCRIPTIONS.get(values[hover_index])
            if desc:
                self._show_style_hover_tip(desc, hover_index, lb_path)
            else:
                self._destroy_style_hover_tip()
        except Exception:
            self._destroy_style_hover_tip()
        finally:
            if self._style_hover_stop:
                self._style_hover_after = None
            else:
                try:
                    self._style_hover_after = self.root.after(80, self._update_style_hover)
                except tk.TclError:
                    pass

    def _show_style_hover_tip(self, desc, hover_index, lb_path):
        """在选项右侧显示解释悬浮窗（深色底浅字）。lb_path 为 listbox 的 Tcl 路径"""
        try:
            # 定位：紧贴下拉框右侧（无缝隙），垂直对齐鼠标所在选项行（2026-08-14 用户定）
            cx = self.style_combo.winfo_rootx()
            cw = self.style_combo.winfo_width()
            # 选项行在 listbox 中的屏幕位置
            bbox = self.root.tk.call(lb_path, "bbox", int(hover_index))
            if bbox:
                ly = int(self.root.tk.call("winfo", "rooty", lb_path))
                by1 = int(bbox[1]); by2 = int(bbox[3])
                y = ly + by1 + (by2 - by1) // 2  # 该行垂直中心
            else:
                y = self.style_combo.winfo_rooty() + self.style_combo.winfo_height() // 2
            tip = self._style_hover_tip
            if tip is None or not tip.winfo_exists():
                tip = tk.Toplevel(self.style_combo)
                tip.wm_overrideredirect(True)
                tip.attributes("-topmost", True)
                ttk.Label(tip, text="", justify=tk.LEFT, background="#2b2f36",
                          foreground="#e0e0e0", borderwidth=1, relief="solid",
                          wraplength=_s(280), padding=(_s(8), _s(6))).pack()
                self._style_hover_tip = tip
            x = cx + cw   # 紧贴下拉框右边（无缝隙）
            # 屏幕边缘防溢出
            tip.update_idletasks()
            tw, th = tip.winfo_reqwidth(), tip.winfo_reqheight()
            sw, sh = tip.winfo_screenwidth(), tip.winfo_screenheight()
            if x + tw > sw:
                x = cx - tw  # 放左侧（紧贴）
            if y + th > sh:
                y = max(0, sh - th)
            tip.winfo_children()[0].config(text=desc)
            tip.wm_geometry(f"+{x}+{y}")
        except (tk.TclError, Exception):
            self._destroy_style_hover_tip()


    def _update_after_right(self, answer):
        """游戏助手处理完成后的UI更新（占位用 search 可靠定位，不再猜行号）"""
        self.system_text.config(state=NORMAL)
        try:
            idx = self.system_text.search("[正在检索...]", "1.0", stopindex=END)
            if idx:
                lineno = int(idx.split(".")[0])
                self.system_text.delete(f"{lineno}.0", f"{lineno + 1}.0")
        except tk.TclError:
            pass
        self.system_text.config(state=DISABLED)

        self.append_system(answer)
        self._set_right_busy(False)
        self.right_input.focus()

    def on_debug_toggle(self):
        """切换调试模式"""
        enabled = self.debug_var.get()
        set_debug_mode(enabled)
        if enabled:
            from .debug_window import toggle_debug
            toggle_debug(self.root)
            self.append_system("调试模式已开启 — API调用将实时显示在调试窗口中")
        else:
            from .debug_window import get_debug_window
            dw = get_debug_window()
            if dw:
                dw.hide()
            self.append_system("调试模式已关闭")

    def on_manual_save(self):
        """手动保存（2026-08-14）：选择存档位保存当前进度；选已有存档位则确认覆盖。
        卡片式槽位选择：Canvas 圆角卡片（复用状态卡片圆角画法），点击选中高亮"""
        if self.is_processing():
            messagebox.showinfo("请稍候", "当前回合还在处理中，请等待完成后再保存。")
            return
        from ..save_manager import list_saves
        saves = list_saves()
        dlg = tk.Toplevel(self.root)
        dlg.title("手动保存")
        dlg.transient(self.root)
        dlg.grab_set()
        from . import place_window
        place_window(dlg, _s(620), _s(580), min_w=_s(520), min_h=_s(480), parent=self.root)
        ttk.Label(dlg, text="选择保存到哪个存档位：", font=FONT_BODY).pack(anchor=W, padx=_s(16), pady=(_s(14), _s(8)))

        # 卡片列表区（Canvas 画圆角卡片，点击选中）
        cards_frame = tk.Frame(dlg, bg=COLOR_BG_PANEL)
        cards_frame.pack(fill=BOTH, expand=YES, padx=_s(16), pady=(_s(0), _s(8)))
        canvas = tk.Canvas(cards_frame, bg=COLOR_BG_PANEL, highlightthickness=0)
        scrollbar = ttk.Scrollbar(cards_frame, orient=VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=RIGHT, fill=Y)
        canvas.pack(side=LEFT, fill=BOTH, expand=YES)
        inner = tk.Frame(canvas, bg=COLOR_BG_PANEL)
        canvas_win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(canvas_win, width=e.width))

        # 滚轮滚动槽位列表（2026-08-14）：任意卡片/空白处滚轮滚动内容。
        # 灵敏度调低：每次滚 3 个单位（约半张卡片），细腻不跳；方向上滚看上方、下滚看下方
        WHEEL_STEP = 3
        def _wheel_scroll(event, units=None):
            try:
                if units is not None:  # Linux Button-4/5
                    canvas.yview_scroll(-units * WHEEL_STEP, "units")
                else:
                    canvas.yview_scroll(-(event.delta // 120) * WHEEL_STEP, "units")
            except tk.TclError:
                pass
            return "break"
        canvas.bind("<MouseWheel>", _wheel_scroll)
        canvas.bind("<Button-4>", lambda e: _wheel_scroll(e, 1))   # Linux上滚 → 看上方
        canvas.bind("<Button-5>", lambda e: _wheel_scroll(e, -1))  # Linux下滚 → 看下方

        selected = {"index": None}

        def _select(index):
            # 取消旧的选中高亮
            if selected["index"] is not None:
                _set_card_highlight(selected["index"], False)
            selected["index"] = index
            _set_card_highlight(index, True)

        card_widgets = []  # (index, canvas) 用于撤销高亮

        def _set_card_highlight(index, on):
            for ci, c in card_widgets:
                if ci == index:
                    try:
                        if on:
                            c.configure(bg="#2a3f4f")
                        else:
                            c.configure(bg=CARD_BG)
                        # 重画圆角底（含新底色）+ 选中金色描边
                        c.delete("bg")
                        w = c.winfo_width() or 560
                        c.create_rectangle(_s(2), _s(2), w - _s(2), _s(62), fill=c.cget("bg"),
                                           outline="#17a2b8" if on else CARD_OUTLINE,
                                           width=2 if on else 1, tags="bg")
                        c.tag_lower("bg")
                    except tk.TclError:
                        pass

        # 每张卡片用 tk.Canvas 独立圆角矩形 + 文本，选中时改描边色
        for i, s in enumerate(saves):
            # bg=CARD_BG：比对话框背景深一档，卡片才有层次；文字浅色清晰可见
            card = tk.Canvas(inner, bg=CARD_BG, highlightthickness=0, width=1, height=_s(64))
            card.pack(fill=X, padx=_s(2), pady=_s(4))
            # 卡片上滚轮滚动列表（同 canvas 绑定）
            card.bind("<MouseWheel>", _wheel_scroll)
            card.bind("<Button-4>", lambda e: _wheel_scroll(e, 1))
            card.bind("<Button-5>", lambda e: _wheel_scroll(e, -1))

            def _draw_card_bg(c):
                c.delete("bg")
                w = c.winfo_width() or 560
                c.create_rectangle(_s(2), _s(2), w - _s(2), _s(62), fill=c.cget("bg"),
                                   outline=CARD_OUTLINE, width=1, tags="bg")
                c.tag_lower("bg")  # 矩形降到底层，避免盖住文字
            card.bind("<Configure>", lambda e, c=card: _draw_card_bg(c))
            _draw_card_bg(card)
            # 名称 + 信息
            if s["exists"]:
                title = f"{s['name']}"
                info = f"{s['rounds']}轮 · 最后游玩 {s['last_played']}"
            else:
                title = f"{s['name']}（空槽位）"
                info = "当前无存档，可直接保存"
            card.create_text(12, 16, anchor="w", text=title, fill=COLOR_FG_MAIN,
                             font=FONT_PANEL_TITLE)
            card.create_text(12, 42, anchor="w", text=info, fill=CARD_TITLE_FG,
                             font=FONT_CARD_TITLE)
            card_widgets.append((i, card))
            # 点击选中
            card.bind("<Button-1>", lambda e, idx=i: _select(idx))

        # 底部按钮
        btn_row = ttk.Frame(dlg)
        btn_row.pack(fill=X, padx=_s(16), pady=(_s(4), _s(14)))

        def _do_save():
            idx = selected["index"]
            if idx is None:
                messagebox.showinfo("手动保存", "请先选择一个存档位。", parent=dlg)
                return
            target = saves[idx]["name"]
            if saves[idx]["exists"]:
                if not messagebox.askyesno(
                        "确认覆盖",
                        f"存档位「{target}」已有存档（{saves[idx]['rounds']}轮）。\n"
                        "确定要覆盖原有存档吗？",
                        parent=dlg):
                    return
            dlg.destroy()
            ok = self.game.save_to_slot(target)
            if ok:
                self.append_system(f"[已手动保存到 {target}]")
            else:
                messagebox.showerror("手动保存", f"保存到 {target} 失败", parent=self.root)

        ttk.Button(btn_row, text="保存到选中槽位", command=_do_save,
                   bootstyle=INFO, width=_s(18)).pack(side=LEFT, padx=(0, _s(8)))
        ttk.Button(btn_row, text="取消", command=dlg.destroy, width=_s(10)).pack(side=LEFT)

    def on_open_history(self):
        """叙事日志（2026-08-15）：只读窗口按轮次回看全部历史"""
        dlg = tk.Toplevel(self.root)
        dlg.title("叙事日志")
        dlg.transient(self.root)
        from . import place_window
        place_window(dlg, _s(760), _s(720), min_w=_s(520), min_h=_s(400), parent=self.root)
        ttk.Label(dlg, text="冒险历史记录", font=FONT_PANEL_TITLE).pack(anchor=W, padx=_s(12), pady=(_s(10), _s(6)))
        wrap = ttk.Frame(dlg)
        wrap.pack(fill=BOTH, expand=YES, padx=_s(10), pady=_s(8))
        text = tk.Text(wrap, wrap=WORD, state=DISABLED, font=FONT_BODY,
                       bg=COLOR_BG_TEXT, fg=COLOR_FG_MAIN, padx=_s(14), pady=_s(12))
        text.pack(side=LEFT, fill=BOTH, expand=YES)
        sb = ttk.Scrollbar(wrap, orient=VERTICAL, command=text.yview)
        sb.pack(side=RIGHT, fill=Y)
        text.configure(yscrollcommand=sb.set)
        text.tag_config("head", foreground=COLOR_TAG_INPUT, font=FONT_PANEL_TITLE)
        text.tag_config("input", foreground=COLOR_TAG_INPUT)
        text.config(state=NORMAL)
        for e in self.game.action_history:
            text.insert(END, f"\n──────── 第{e.get('round','?')}轮 ────────\n", "head")
            text.insert(END, f"> {e.get('input','')}\n\n", "input")
            text.insert(END, f"{e.get('narrative','')}\n")
        text.insert(END, "\n（END）\n")
        text.config(state=DISABLED)

    def on_open_world_doc(self):
        """世界档案（2026-08-15）：世界观总述/剧情线/NPC关系/世界大事记 只读展示"""
        dlg = tk.Toplevel(self.root)
        dlg.title("世界档案")
        dlg.transient(self.root)
        from . import place_window
        place_window(dlg, _s(820), _s(780), min_w=_s(560), min_h=_s(440), parent=self.root)
        wrap = ttk.Frame(dlg)
        wrap.pack(fill=BOTH, expand=YES, padx=_s(10), pady=_s(8))
        text = tk.Text(wrap, wrap=WORD, state=DISABLED, font=FONT_BODY,
                       bg=COLOR_BG_TEXT, fg=COLOR_FG_MAIN, padx=_s(14), pady=_s(12))
        text.pack(side=LEFT, fill=BOTH, expand=YES)
        sb = ttk.Scrollbar(wrap, orient=VERTICAL, command=text.yview)
        sb.pack(side=RIGHT, fill=Y)
        text.configure(yscrollcommand=sb.set)
        text.tag_config("head", foreground=COLOR_TAG_INPUT, font=FONT_PANEL_TITLE)
        text.config(state=NORMAL)
        # 世界观
        text.insert(END, "【世界】\n", "head")
        text.insert(END, f"{self.game.world_template.get('world_description','（无）')}\n\n")
        # 剧情线
        text.insert(END, "【剧情线】\n", "head")
        threads = self.game.get_story_threads()
        if threads:
            status_cn = {"active": "进行中", "dormant": "蛰伏", "resolved": "已了结"}
            for t in threads:
                if not isinstance(t, dict):
                    continue
                tag = status_cn.get(t.get("status", ""), "")
                text.insert(END, f"• {t.get('title','')}（{tag}·{t.get('stage','')}）\n")
                text.insert(END, f"  下一步：{t.get('next_beat','')}\n")
        else:
            text.insert(END, "（暂无剧情线）\n")
        text.insert(END, "\n")
        # NPC 关系
        text.insert(END, "【人物关系】\n", "head")
        for npc_id, npc in list(self.game.npcs.items()):
            name = npc.get("name", npc_id)
            role = npc.get("role", "")
            rel = npc.get("relationship_to_player", "")
            text.insert(END, f"• {name}（{role}）：{rel}\n")
            mem = self.game.get_npc_memories_for_p1(npc_id)
            strong = [m for m in mem if isinstance(m, dict) and m.get("importance", 0) >= 3]
            if strong:
                text.insert(END, f"  · 记忆：{strong[0].get('event','')}\n")
        text.insert(END, "\n")
        # 世界大事记
        text.insert(END, "【世界大事记】\n", "head")
        events = [f.get("content", "") for f in self.game.known_facts
                  if is_world_event(f)]
        if events:
            for ev in events[-10:]:
                text.insert(END, f"• {ev}\n")
        else:
            text.insert(END, "（尚无世界大事）\n")
        text.insert(END, "\n")
        text.config(state=DISABLED)

    def on_rollback(self):
        """回退到上一回合（撤销本回合）：恢复快照 + 删除叙事区本回合内容 + 刷新HUD。
        仅当本回合输出全部完成后可点（_set_left_busy 已控制处理中禁用）"""
        if self.is_processing():
            messagebox.showinfo("请稍候", "当前回合还在处理中，请等待完成后再回退。")
            return
        if not self.game.has_rollback_snapshot():
            messagebox.showinfo("回退", "没有可回退的上一回合。")
            return
        if not messagebox.askyesno("回退", "确定回退到上一回合吗？本回合的叙事、状态与存档将撤销。"):
            return
        if not self.game.restore_rollback_snapshot():
            messagebox.showinfo("回退", "回退失败：快照不可用。")
            return
        # 删除叙事区本回合内容（从提交时记录的 mark 到末尾）
        try:
            mark = self._round_mark or "1.0"
            self.narrative_text.config(state=NORMAL)
            self.narrative_text.delete(mark, END)
            self.narrative_text.config(state=DISABLED)
        except tk.TclError:
            pass
        self._round_mark = None
        # 刷新HUD：轮次/位置/状态栏（skip_narrative 不重复 append，_set_left_busy(False) 放开入口）
        self._update_after_left("", self.game.player_state, skip_narrative=True)
        self.append_system("[已回退到上一回合]")

    def on_return_menu_click(self):
        """返回主菜单"""
        # 修复：回合/查询处理中禁止返回，避免后台线程写已销毁的UI
        if self.is_processing():
            self.append_system("[当前正在处理中，请稍候片刻再返回]")
            return
        if messagebox.askyesno("确认", "确定要返回主菜单吗？当前进度已自动保存。"):
            # 不再在此预销毁 frame——统一由 main._clear_frame 负责销毁（预销毁会导致
            # 主菜单双 destroy 已销毁控件而崩溃，2026-08-14 修复）
            self.on_return_menu()
