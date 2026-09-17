# -*- coding: utf-8 -*-
"""
UI 共用辅助（P2 界面精进）
- 统一的窗口定位：任何分辨率下窗口完整可见、居中、不越界
- 统一的视觉常量：字体族 / 基准字号 / 文本区配色，各处不再散落硬编码
"""
import tkinter as tk

# ===== 分辨率缩放（2026-08-15：更大分辨率下 UI 元素等比例放大）=====
BASE_RESOLUTION_HEIGHT = 1350  # 基准分辨率 1800×1350 的高


def get_scale():
    """缩放因子：当前分辨率高 / 基准高(1350)。1800×1350→1.0，2560×1920→1.422。
    解析失败/缺失兜底 1.0（保持旧行为不缩放）"""
    try:
        from ..config import get_config
        res = get_config().get("ui", "resolution", default="")
        w, h = parse_resolution(res, default=(1800, 1350))
        return max(1.0, round(h / BASE_RESOLUTION_HEIGHT, 4))
    except Exception:
        return 1.0


def font_size(base):
    """基准字号按缩放因子放大（返回整数）"""
    return round(base * get_scale())


# 全局缩放因子（各界面 import 时读取一次）
SCALE = get_scale()

# ===== 全局字体（基准字号由 main.py 启动时统一设定一次）=====
FONT_FAMILY = "Microsoft YaHei"
BASE_FONT_SIZE = 11
BASE_FONT_SIZE_SCALED = font_size(BASE_FONT_SIZE)  # 2026-08-15 随分辨率放大

# ===== 深色文本区配色（贴近 ttkbootstrap darkly 主题）=====
COLOR_BG_TEXT = "#1a1a2e"      # 叙事区背景
COLOR_BG_TEXT_ALT = "#16213e"  # 查询/对话区背景
COLOR_BG_CODE = "#1e1e1e"      # 调试窗口代码区背景
COLOR_FG_MAIN = "#e0e0e0"      # 主文字
COLOR_FG_DIM = "#c0c0c0"       # 次要文字
COLOR_FG_CODE = "#d4d4d4"      # 调试窗口代码区文字
COLOR_BG_PANEL = "#222222"     # 滚动画布底色（贴近 darkly 背景）

# ===== 叙事/对话 tag 配色（2026-08-15 三色统一：红/蓝/青蓝，对齐 darkly 的 danger/primary/info）=====
COLOR_TAG_INPUT = "#4582ec"      # 玩家输入（蓝 primary）
COLOR_TAG_SYSTEM = "#9a9aad"     # 系统提示（灰，保留）
COLOR_TAG_ERROR = "#d9534f"      # 错误（红 danger）
COLOR_TAG_ASSISTANT = "#17a2b8"  # 助手回复（青蓝 info）


# ===== 游戏窗口分辨率锁定（2026-08-05 用户需求：像游戏一样把分辨率设死）=====
# 2026-08-14 用户定默认 1800×1350；2026-08-15 追加更大的同比例(4:3)选项
DEFAULT_RESOLUTION = "1800×1350"
# 分辨率选项（主菜单设置区下拉框用；全部 4:3 长宽比，越大越高清，重启后生效）
RESOLUTION_OPTIONS = [
    "1800×1350",
    "1920×1440",
    "2048×1536",
    "2560×1920",
]

# ===== API 厂商预设（2026-09-17：多厂商支持）=====
# 全部走 OpenAI 兼容的 /chat/completions 协议，只有 api_base 和模型名不同。
# 键名与 config 的 api_keys 槽位一致；models 只是下拉建议，输入框可自由填写。
# needs_key=False 的本地推理服务无需密钥（界面会自动填占位符）。
PROVIDER_PRESETS = {
    "deepseek": {
        "label": "DeepSeek",
        "api_base": "https://api.deepseek.com/v1",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"],
        "key_url": "https://platform.deepseek.com/api_keys",
    },
    "openai": {
        "label": "OpenAI",
        "api_base": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"],
        "key_url": "https://platform.openai.com/api-keys",
    },
    "moonshot": {
        "label": "Moonshot 月之暗面 (Kimi)",
        "api_base": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "key_url": "https://platform.moonshot.cn/console/api-keys",
    },
    "zhipu": {
        "label": "智谱 GLM",
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-plus", "glm-4-air", "glm-4-flash", "glm-4-long"],
        "key_url": "https://open.bigmodel.cn/usercenter/apikeys",
    },
    "qwen": {
        "label": "通义千问 (DashScope)",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-max", "qwen-plus", "qwen-turbo", "qwen-long"],
        "key_url": "https://bailian.console.aliyun.com/",
    },
    "siliconflow": {
        "label": "SiliconFlow 硅基流动",
        "api_base": "https://api.siliconflow.cn/v1",
        "models": ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct"],
        "key_url": "https://cloud.siliconflow.cn/account/ak",
    },
    "gemini": {
        "label": "Google Gemini",
        "api_base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "models": ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"],
        "key_url": "https://aistudio.google.com/apikey",
    },
    "ollama": {
        "label": "Ollama（本地，无需密钥）",
        "api_base": "http://localhost:11434/v1",
        "models": ["qwen2.5:14b", "llama3.1:8b"],
        "key_url": "",
        "needs_key": False,
    },
    "lmstudio": {
        "label": "LM Studio（本地，无需密钥）",
        "api_base": "http://localhost:1234/v1",
        "models": [],
        "key_url": "",
        "needs_key": False,
    },
    "custom": {
        "label": "自定义 / 中转站",
        "api_base": "",
        "models": [],
        "key_url": "",
    },
}

# 本地服务无需真实密钥，填此占位符满足 API 层非空校验
LOCAL_PLACEHOLDER_KEY = "local-no-key"

# ===== 统一字号梯度（2026-08-06 用户定案：全窗口字体收编，各处不再散装硬编码）=====
# 基准字号（不缩放）；实际使用用 *_SCALED（乘 SCALE，随分辨率放大）
# 2026-08-15 用户定：叙事/状态栏字号整体加大（BODY 12→14、CARD_TITLE 10→12）
FONT_SIZE_TOP = 12          # 顶栏
FONT_SIZE_CARD_TITLE = 12   # 状态卡片分类名
FONT_SIZE_BODY = 14         # 正文（叙事/对话/输入/卡片值）
FONT_SIZE_PANEL_TITLE = 13  # 面板标题（粗体使用处自加 "bold"）

# 缩放后的实际字号（2026-08-15：随分辨率等比例放大；1800×1350 时等于基准值）
FONT_SIZE_TOP_SCALED = font_size(FONT_SIZE_TOP)
FONT_SIZE_CARD_TITLE_SCALED = font_size(FONT_SIZE_CARD_TITLE)
FONT_SIZE_BODY_SCALED = font_size(FONT_SIZE_BODY)
FONT_SIZE_PANEL_TITLE_SCALED = font_size(FONT_SIZE_PANEL_TITLE)

# ===== 深色卡片配色（darkly协调：卡片底比主题背景更深；立绘面板已2026-08-14封存）=====
CARD_BG = "#1c1f24"        # 卡片底（比darkly背景#222再深一点）
CARD_OUTLINE = "#2e3641"   # 描边（微弱层次）
CARD_TITLE_FG = "#7f9bb3"  # 卡片分类名（灰蓝次要色）


def parse_resolution(text, default=(1920, 1080)):
    """解析分辨率文本（如"1920×1080"）为 (宽, 高)。
    乘号 × / 字母 x / X 都认；配置缺失或解析失败时兜底 default"""
    try:
        parts = str(text).strip().lower().replace("x", "×").split("×")
        w, h = int(parts[0]), int(parts[1])
        if w < 640 or h < 480:
            raise ValueError(f"分辨率过小: {w}x{h}")
        return w, h
    except Exception:
        return default


def compute_geometry(screen_w, screen_h, want_w, want_h,
                     min_w=640, min_h=480, max_ratio=0.85, parent_geom=None):
    """
    纯函数：计算居中且不越界的 geometry 字符串。

    策略：尺寸不超过屏幕的 max_ratio（默认85%），不小于 min_w/min_h
    （极小屏幕上"最小值"让位于屏幕上限）；优先相对父窗口居中，
    否则相对屏幕居中；最后把 x/y 钳制在屏幕内，确保右下不越界。

    参数:
        screen_w/screen_h: 屏幕像素尺寸
        want_w/want_h:     期望窗口尺寸
        min_w/min_h:       合理最小尺寸
        max_ratio:         尺寸占屏幕比例上限
        parent_geom:       可选 (x, y, w, h)，父窗口位置尺寸（Toplevel 相对父居中）
    返回:
        "WxH+X+Y" 形式的 geometry 字符串
    """
    cap_w = max(1, int(screen_w * max_ratio))
    cap_h = max(1, int(screen_h * max_ratio))
    w = min(max(min_w, min(int(want_w), cap_w)), cap_w)
    h = min(max(min_h, min(int(want_h), cap_h)), cap_h)

    if parent_geom:
        px, py, pw, ph = parent_geom
        if pw > 1 and ph > 1:  # 父窗口已布局完成才相对父居中
            x = px + (pw - w) // 2
            y = py + (ph - h) // 2
        else:
            x = (screen_w - w) // 2
            y = (screen_h - h) // 2
    else:
        x = (screen_w - w) // 2
        y = (screen_h - h) // 2

    # 钳制：不为负、右下不越界
    x = max(0, min(x, screen_w - w))
    y = max(0, min(y, screen_h - h))
    return f"{w}x{h}+{x}+{y}"


def place_window(win, want_w, want_h, min_w=640, min_h=480,
                 max_ratio=0.85, parent=None):
    """
    读取屏幕尺寸，计算并应用居中且不越界的 geometry。
    Toplevel 传 parent 可相对父窗口居中；父窗口尚未布局时自动退化为屏幕居中。
    """
    try:
        win.update_idletasks()
        screen_w = win.winfo_screenwidth()
        screen_h = win.winfo_screenheight()
        parent_geom = None
        if parent is not None:
            try:
                parent.update_idletasks()
                parent_geom = (parent.winfo_rootx(), parent.winfo_rooty(),
                               parent.winfo_width(), parent.winfo_height())
            except tk.TclError:
                parent_geom = None  # 父窗口异常时退化为屏幕居中
        win.geometry(compute_geometry(screen_w, screen_h, want_w, want_h,
                                      min_w, min_h, max_ratio, parent_geom))
    except tk.TclError:
        pass  # 窗口已销毁等情况，保持默认位置
