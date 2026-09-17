"""
游戏状态管理器
运行时状态管理，封装所有游戏数据的读写
"""
import copy
import math
import re
import threading
from datetime import datetime
from .save_manager import SaveManager
from .config import get_config
from .vocab import is_worldview_base

# NPC档案写操作锁：保护异步任务（P4C记忆巩固）与主循环对npcs的并发修改
# 锁定顺序约定：NPCS_LOCK 只用于内存字典修改（O(1)），持锁期间绝不调用API或写盘
NPCS_LOCK = threading.RLock()

# 地图数据写操作锁：保护P12异步线程对map_data的并发修改
# 同样只用于内存修改，持锁期间不调API/不写盘/不渲染
MAP_LOCK = threading.RLock()

# 玩家状态/已知事实锁：保护P3/P11等后台线程与主循环对 player_state / known_facts 的并发读写
# 只用于内存修改（O(1)），持锁期间不调API/不写盘
STATE_LOCK = threading.RLock()


# 单个NPC记忆条数上限，超过则触发巩固（见 docs/npc_memory_design.md 第六节）
MEMORY_CONSOLIDATE_THRESHOLD = 30


def select_memories_for_p1(memory_log, max_items=4):
    """P1注入用记忆选取（纯函数）：最近2条 + importance最高的2条，去重后≤max_items条"""
    if not memory_log or not isinstance(memory_log, list):
        return []
    n = len(memory_log)
    picked_idx = set()
    result = []
    # 最近2条（保持原顺序）
    recent_idx = list(range(max(0, n - 2), n))
    # importance最高的2条（同分时取较新的）
    top_idx = sorted(range(n), key=lambda i: (-(memory_log[i].get("importance", 1) or 1), -i))[:2]
    for i in recent_idx + top_idx:
        if i not in picked_idx:
            picked_idx.add(i)
            result.append(memory_log[i])
        if len(result) >= max_items:
            break
    return result


# ===== 游戏内日历：季节/天气动态（见 docs/season_weather_design.md）=====

# 默认历法：一年4季×30天=360天（自定义历法经 world_template.calendar 留接口）
SEASONS = ["春季", "夏季", "秋季", "冬季"]
DAYS_PER_SEASON = 30
# time_passed 解析失败/缺失时的兜底：每轮按0.25天（约6小时）推进
TIME_FALLBACK_DAYS = 0.25

# 中文数字映射（含"两"）
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_to_int(s):
    """中文/阿拉伯数字转整数（支持"十五""二十""二十五"等复合），失败返回None"""
    s = s.strip()
    if s.isdigit():
        return int(s)
    if s in _CN_NUM:
        return _CN_NUM[s]
    # 复合：X十 / 十X / X十Y
    if "十" in s:
        left, _, right = s.partition("十")
        if (not left or left in _CN_NUM) and (not right or right in _CN_NUM):
            tens = _CN_NUM.get(left, 1) if left else 1
            ones = _CN_NUM.get(right, 0) if right else 0
            return tens * 10 + ones
    return None


# English number words used by parse_time_passed. "half" is deliberately absent:
# it is a fraction, not a count, and the fractions have dedicated branches below.
_EN_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
           "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _en_to_num(s):
    """English number word or decimal string to a number; None when unrecognized."""
    s = s.strip().lower()
    if s in _EN_NUM:
        return _EN_NUM[s]
    try:
        return float(s)
    except ValueError:
        return None


def select_facts_for_p1(known_facts, limit=40):
    """P1/P14注入用事实选取（纯函数，2026-08-14 截断策略）：
    known_facts 追加式增长，全量注入会让 P1 前缀越来越贵（HANDOVER 7.2.4）。
    选取（去重，最多 limit 条）：
    1. 世界观基盘（source含'世界观'）——叙事连贯的根基，永不失
    2. 最近 limit 条的一半（较新信息，叙事仍在用）
    3. 高置信 high 的补足剩余额度（重要结论保留）
    返回：截断后的原文（dict/str），顺序：世界观基盘 → 最近的 → 高置信"""
    if not known_facts or not isinstance(known_facts, list):
        return []
    limit = max(10, limit)
    # 世界观基盘（低置信/无来源的世界观也保留）
    base = [f for f in known_facts if is_worldview_base(f)]
    recent = list(known_facts[-limit:])
    picked = []
    seen = set()
    def _add(f):
        key = f.get("content") if isinstance(f, dict) else f
        if key not in seen:
            seen.add(key)
            picked.append(f)
    for f in base:
        _add(f)
    if len(picked) < limit:
        n_recent = min(len(recent), (limit - len(picked)) // 2)
        for f in recent[:n_recent]:
            _add(f)
    if len(picked) < limit:
        high = [f for f in known_facts
                if (f.get("confidence") if isinstance(f, dict) else "") == "high"]
        for f in high:
            if len(picked) >= limit:
                break
            _add(f)
    return picked


def parse_time_passed(text):
    """解析P3估算的本轮经过时间，返回折算天数（浮点，纯函数）
    规则见 docs/season_weather_design.md 第五节；解析失败/缺失兜底0.25天

    Understands both English and Chinese. English is what the model emits now;
    Chinese is kept so pre-English saves and mixed output still parse."""
    if not text or not isinstance(text, str):
        return TIME_FALLBACK_DAYS
    t = text.strip()
    tl = t.lower()
    # 第二天/次日/翌日（须在通用N天之前，防止"第二天"被"二天"误匹配为2天）
    # "the next day" must likewise precede the generic N-days rule
    if any(k in t for k in ("第二天", "次日", "翌日")) or \
            re.search(r'\b(?:the\s+)?(?:next|following)\s+day\b', tl):
        return 1.0
    # 半天（"半"不是数字，通用N天规则匹配不到）/ half a day
    if "半天" in t or re.search(r'\bhalf\s+an?\s+day\b', tl):
        return 0.5
    # N天 / N days
    m = re.search(r'([一二两三四五六七八九十\d]+)\s*天', t) or \
        re.search(r'\b([a-z]+|\d+(?:\.\d+)?)\s*days?\b', tl)
    if m:
        n = _cn_to_int(m.group(1))
        if n is None:
            n = _en_to_num(m.group(1))
        if n:
            return float(n)
    # 一夜/一晚/整夜/通宵（按半天计）/ a night / overnight
    if re.search(r'一夜|一晚|整夜|通宵', t) or \
            re.search(r'\b(?:an?|one)\s+night\b|\bovernight\b|\ball\s+night\b', tl):
        return 0.5
    # 半小时 / half an hour
    if "半小时" in t or re.search(r'\bhalf\s+an?\s+hour\b|\b30\s+minutes?\b', tl):
        return 0.5 / 24.0
    # N刻钟（1刻钟=0.25小时）
    # a quarter hour / quarter of an hour —— 必须排在 N hours 之前：
    # 中文的"刻钟"与"小时"是不同词，英文的 "quarter of an hour" 却含 "hour"，
    # 否则会被通用规则当成"1小时"
    m = re.search(r'([一二两三四五六七八九十\d]+)\s*刻钟?', t)
    if m:
        n = _cn_to_int(m.group(1))
        if n:
            return n * 0.25 / 24.0
    if re.search(r'\b(?:a\s+)?quarter\s+(?:of\s+an?\s+)?hour\b', tl):
        return 0.25 / 24.0
    # N小时 / N hours
    m = re.search(r'([一二两三四五六七八九十\d]+)\s*(?:个)?\s*小时', t) or \
        re.search(r'\b([a-z]+|\d+(?:\.\d+)?)\s*hours?\b', tl)
    if m:
        n = _cn_to_int(m.group(1))
        if n is None:
            n = _en_to_num(m.group(1))
        if n:
            return n / 24.0
    # N分钟 / N minutes
    m = re.search(r'([一二两三四五六七八九十\d]+)\s*分钟', t) or \
        re.search(r'\b([a-z]+|\d+(?:\.\d+)?)\s*minutes?\b', tl)
    if m:
        n = _cn_to_int(m.group(1))
        if n is None:
            n = _en_to_num(m.group(1))
        if n:
            return n / 60.0 / 24.0
    return TIME_FALLBACK_DAYS


def get_season_for_day(game_day, calendar=None):
    """由游戏内天数计算季节（纯函数）
    calendar: 可选自定义历法 {"seasons": [{"name": "...", "days": N}, ...]}，缺省用默认4季×30天"""
    try:
        day = max(1, int(game_day))
    except (TypeError, ValueError):
        day = 1
    seasons = None
    if isinstance(calendar, dict):
        raw = calendar.get("seasons")
        if isinstance(raw, list) and raw and all(
                isinstance(s, dict) and s.get("name") and (s.get("days") or 0) > 0 for s in raw):
            seasons = [(str(s["name"]), int(s["days"])) for s in raw]
    if not seasons:
        seasons = [(name, DAYS_PER_SEASON) for name in SEASONS]
    # 按各季天数循环推进
    total = sum(d for _n, d in seasons)
    pos = (day - 1) % total  # 年内位置（0起）
    for name, days in seasons:
        if pos < days:
            return name
        pos -= days
    return seasons[-1][0]  # 理论不可达，兜底


class GameState:
    """运行时游戏状态"""

    def __init__(self, save_name):
        self.save_manager = SaveManager(save_name)
        self.config = get_config()

        # 运行时数据（从存档加载或初始化）
        self.world_template = {}
        self.player_profile = {}
        self.player_state = {}
        self.known_facts = []
        self.action_history = []
        self.settings = {}
        self.npcs = {}
        self.meta = {}
        self.current_round = 0
        self.story_threads = {"threads": []}
        # 地图数据（P12地图师维护，见 docs/p12_map_design.md）：
        # places = {地名: {x, y, icon_subject, icon_file}}；far_places = [{direction, name}]
        self.map_data = {"places": {}, "far_places": [], "version": 0}

        # 本轮新数据（等待保存）
        self.pending_facts = []
        self.pending_npc_updates = {}

        # 回合回退快照（2026-08-14）：每回合真正开始前保存"上一回合结束态"，供回退按钮撤销本回合
        self._rollback_snapshot = None

    def load(self):
        """从存档加载所有数据"""
        self.world_template = self.save_manager.load_world_template()
        self.player_profile = self.save_manager.load_player_profile()
        self.player_state = self.save_manager.load_player_state()
        self.known_facts = self.save_manager.load_known_facts()
        self.action_history = self.save_manager.load_action_history()
        self.settings = self.save_manager.load_settings()
        # 叙事风格旧值迁移（2026-08-14）：'gritty写实' 已改名为 '冷硬写实'，读档时映射并落盘
        if self.settings.get("narrative_style") == "gritty写实":
            self.settings["narrative_style"] = "冷硬写实"
            try:
                self.save_manager.save_settings(self.settings)
            except Exception:
                pass
        self.npcs = self.save_manager.load_all_npcs()
        self.meta = self.save_manager.load_meta() if hasattr(self.save_manager, 'load_meta') else {}
        self.current_round = self.meta.get("rounds", 0)
        self.story_threads = self.save_manager.load_story_threads()
        # 地图数据（旧存档无此文件时初始化为空结构，向后兼容）
        self.map_data = self.save_manager.load_map_data()
        if not isinstance(self.map_data, dict):
            self.map_data = {"places": {}, "far_places": [], "version": 0}
        if not isinstance(self.map_data.get("places"), dict):
            self.map_data["places"] = {}
        if not isinstance(self.map_data.get("far_places"), list):
            self.map_data["far_places"] = []
        if not isinstance(self.map_data.get("version"), int):
            self.map_data["version"] = 0

    def init_new(self, world_data, player_info, settings):
        """初始化新游戏状态"""
        self.save_manager.init_new_save(world_data, player_info, settings)
        self.load()

    def get_npcs_in_scene(self):
        """获取当前场景中的NPC（加锁快照，防止异步任务并发修改导致RuntimeError）"""
        with NPCS_LOCK:
            scene_people = self.player_state.get("current_scene_people", "")
            result = {}
            for npc_id, npc in list(self.npcs.items()):
                if npc.get("name", "") in scene_people:
                    result[npc_id] = npc
            return result

    def get_known_facts_summary(self, limit=None):
        """获取已知事实的文本摘要列表。
        limit 非 None 时先 select_facts_for_p1 截断（P1/P14 注入用，防止 known_facts 无限增长
        推高 prompt 前缀；P2 查询用全量，调用处不传 limit）"""
        # 锁内快照：P11/P3后台线程可能并发 add_fact，直接遍历会 RuntimeError
        with STATE_LOCK:
            snapshot = list(self.known_facts)
        if limit is not None:
            src = select_facts_for_p1(snapshot, limit)
        else:
            src = snapshot
        result = []
        for f in src:
            if isinstance(f, dict):
                result.append(f.get("content", str(f)))
            else:
                result.append(str(f))
        return result

    def get_recent_history(self, limit=None):
        """获取最近N轮历史"""
        if limit is None:
            limit = self.settings.get("history_limit", 10)
        return self.action_history[-limit:] if len(self.action_history) > limit else self.action_history[:]

    def add_fact(self, fact_data):
        """添加一条新事实（加锁：P3/P11后台线程与主循环并发 append）"""
        with STATE_LOCK:
            if isinstance(fact_data, dict) and "content" in fact_data:
                self.known_facts.append(fact_data)
            elif isinstance(fact_data, str):
                self.known_facts.append({"content": fact_data, "source": "叙事"})
            self.pending_facts.append(fact_data)

    def update_player_state(self, new_state):
        """更新玩家状态"""
        with STATE_LOCK:
            self.player_state = new_state

    # ===== 游戏内日历（季节由日历驱动，见 docs/season_weather_design.md）=====

    def get_game_day(self):
        """获取游戏内累计天数（浮点）。旧存档无game_day时初始化：
        已有game_season则对齐到该季节第一天（保持叙事连续），否则从第1天开始"""
        with STATE_LOCK:
            if "game_day" not in self.player_state:
                init_day = 1.0
                season_text = str(self.player_state.get("game_season", ""))
                for i, name in enumerate(SEASONS):
                    if name[0] in season_text:  # 如 '秋' in '深秋'
                        init_day = float(i * DAYS_PER_SEASON + 1)
                        break
                self.player_state["game_day"] = init_day
            try:
                return float(self.player_state.get("game_day", 1.0))
            except (TypeError, ValueError):
                self.player_state["game_day"] = 1.0
                return 1.0

    def add_game_days(self, days):
        """累加游戏内天数（每轮P3估算的本轮经过时间；加锁防P3线程与主循环并发读写）"""
        with STATE_LOCK:
            try:
                days = float(days)
            except (TypeError, ValueError):
                days = TIME_FALLBACK_DAYS
            if days <= 0:
                days = TIME_FALLBACK_DAYS
            self.player_state["game_day"] = self.get_game_day() + days
            return self.player_state["game_day"]

    def get_season(self):
        """当前季节（由日历计算，world_template.calendar可自定义历法）"""
        return get_season_for_day(self.get_game_day(), self.world_template.get("calendar"))

    def get_date_display(self):
        """日期显示文本：第X天 · 季节（game_day浮点存储，显示取整）"""
        return f"第{int(self.get_game_day())}天 · {self.get_season()}"

    def get_game_time(self):
        """当前时段（2026-08-15 game_time 联动日历）：由 game_day 的小数部分推算。
        一天分 4 段各 6 小时：清晨 0-6 / 正午 6-12 / 傍晚 12-18 / 深夜 18-24。
        game_day 缺失/非法时回退"正午"（保持旧存档兼容）"""
        try:
            frac = self.get_game_day() - int(self.get_game_day())
            hour = int(frac * 24) % 24
        except (TypeError, ValueError):
            return "正午"
        if hour < 6:
            return "清晨"
        if hour < 13:   # 6:00-12:59 正午（含12:00）
            return "正午"
        if hour < 19:   # 13:00-18:59 傍晚
            return "傍晚"
        return "深夜"    # 19:00-23:59

    # ===== 剧情线（P11故事师维护，见 docs/story_generator_design.md）=====

    def get_story_threads(self):
        """全部剧情线（含resolved），结构缺失时容错初始化"""
        if not isinstance(self.story_threads, dict):
            self.story_threads = {"threads": []}
        if not isinstance(self.story_threads.get("threads"), list):
            self.story_threads["threads"] = []
        return self.story_threads["threads"]

    def get_active_threads(self):
        """供P1注入的剧情线：active + dormant（resolved不注入）"""
        return [t for t in self.get_story_threads()
                if isinstance(t, dict) and t.get("status") in ("active", "dormant")]

    def update_threads(self, new_list):
        """整体替换剧情线列表（P11产出）。
        清洗规则：无title的丢弃；id去重；status非法默认active；
        保留元信息（同id旧线的created_round/involved若缺则沿用）；
        active硬上限3条，超出按顺序降级为dormant"""
        old_by_id = {t.get("id"): t for t in self.get_story_threads() if isinstance(t, dict)}
        cleaned = []
        seen_ids = set()
        active_count = 0
        for i, t in enumerate(new_list or []):
            if not isinstance(t, dict) or not t.get("title"):
                continue
            tid = str(t.get("id") or f"thread_{i + 1:03d}")
            if tid in seen_ids:
                continue
            seen_ids.add(tid)
            old = old_by_id.get(tid, {})
            status = t.get("status") if t.get("status") in ("active", "dormant", "resolved") else "active"
            # v1硬上限：active≤3，多余的降级为dormant（不删，可复活）
            if status == "active":
                active_count += 1
                if active_count > 3:
                    status = "dormant"
            cleaned.append({
                "id": tid,
                "title": str(t.get("title", "")),
                "summary": str(t.get("summary", "")),
                "stage": str(t.get("stage", "")),
                "next_beat": str(t.get("next_beat", "")),
                "involved": t.get("involved") if isinstance(t.get("involved"), list) else old.get("involved", []),
                "status": status,
                "created_round": t.get("created_round") or old.get("created_round") or self.current_round,
                "updated_round": self.current_round,
            })
        self.story_threads = {"threads": cleaned}
        return cleaned

    def should_run_story_review(self):
        """是否该跑P11剧情回顾（简化规则，见设计文档第二节）：
        轮次>0 且距上次回顾≥10轮；或游戏已开始但还没有任何剧情线（首次孵化，新旧存档通用）"""
        if self.current_round <= 0:
            return False
        try:
            last = int(self.meta.get("last_story_review_round", 0) or 0)
        except (TypeError, ValueError):
            last = 0
        if self.current_round - last >= 10:
            return True
        # 首次孵化：还没有剧情线且游戏已开始
        if last == 0 and not self.get_story_threads():
            return True
        return False

    # ===== 地图数据（P12地图师维护，见 docs/p12_map_design.md）=====

    def get_map_places(self):
        """地名注册表快照 {地名: {x, y, icon_subject, icon_file}}"""
        with MAP_LOCK:
            return {name: dict(info) for name, info in self.map_data["places"].items()}

    def get_far_places(self):
        """远方地点列表快照 [{direction, name}]"""
        with MAP_LOCK:
            return [dict(p) for p in self.map_data["far_places"]]

    def get_map_version(self):
        """地图数据版本号（每次落库自增，供"是否需要重渲染"判断）"""
        with MAP_LOCK:
            return self.map_data.get("version", 0)

    def is_location_registered(self, name):
        """程序门：地名是否已注册（已上图或在远方列表中）"""
        if not name:
            return True  # 空位置不触发
        with MAP_LOCK:
            if name in self.map_data["places"]:
                return True
            return any(p.get("name") == name for p in self.map_data["far_places"])

    def register_place(self, name, x, y, icon_subject, icon_file=None, label=None):
        """落库一个新地点坐标（落库即锁定，一生只定位一次）"""
        with MAP_LOCK:
            self.map_data["places"][name] = {
                "x": int(x), "y": int(y),
                "icon_subject": str(icon_subject),
                "icon_file": icon_file,
                "label": str(label) if label else None,  # 地图显示用短名（P12给出，建议7字内）
            }
            self.map_data["version"] = self.map_data.get("version", 0) + 1
            return self.map_data["version"]

    def register_far_place(self, name, direction, label=None):
        """落库一个远方地点"""
        with MAP_LOCK:
            self.map_data["far_places"].append({"direction": str(direction), "name": str(name),
                                                "label": str(label) if label else None})
            self.map_data["version"] = self.map_data.get("version", 0) + 1
            return self.map_data["version"]

    def set_place_icon(self, name, icon_file):
        """图标生成完成后回写icon_file"""
        with MAP_LOCK:
            if name in self.map_data["places"]:
                self.map_data["places"][name]["icon_file"] = icon_file
                self.map_data["version"] = self.map_data.get("version", 0) + 1
                return self.map_data["version"]
        return None

    def get_player_xy(self):
        """玩家当前位置对应的地图坐标（current_location已注册才有），未注册返回None"""
        loc = str(self.player_state.get("current_location", "")).strip()
        with MAP_LOCK:
            info = self.map_data["places"].get(loc)
            if info:
                return (info["x"], info["y"])
        return None

    def save_map_data(self):
        """地图数据立即持久化（P12落库后不等下一轮save_all）。
        持锁只取快照、锁外写盘——遵守"持锁不写盘"约定，写盘不阻塞 get_map_places 等读"""
        with MAP_LOCK:
            snapshot = copy.deepcopy(self.map_data)
        self.save_manager.save_map_data(snapshot)

    def update_npc(self, npc_id, update_data):
        """更新NPC数据"""
        with NPCS_LOCK:
            if npc_id not in self.npcs:
                self.npcs[npc_id] = {}
            self.npcs[npc_id].update(update_data)
            self.pending_npc_updates[npc_id] = update_data

    def add_npc_from_entity(self, entity):
        """从new_entities创建新NPC"""
        import re
        with NPCS_LOCK:
            # 找到下一个可用的npc_id
            max_num = 0
            for existing_id in self.npcs.keys():
                match = re.match(r'npc_(\d+)', existing_id)
                if match:
                    max_num = max(max_num, int(match.group(1)))
            new_num = max_num + 1
            npc_id = f"npc_{new_num:03d}"

            # 构建NPC基础档案
            npc_data = {
                "npc_id": npc_id,
                "name": entity.get("name", "未知"),
                "role": entity.get("role", "未知"),
                "appearance": entity.get("description", ""),
                "personality": entity.get("personality", "未知"),
                "background": entity.get("background", "未知"),
                "relationship_to_player": entity.get("relationship_to_player", "未知"),
                "psychology_log": [],
                "secrets": entity.get("secrets", [])
            }

            self.npcs[npc_id] = npc_data
            self.pending_npc_updates[npc_id] = npc_data
        return npc_id

    def add_npc_psychology(self, npc_id, entry):
        """追加NPC心理日志"""
        with NPCS_LOCK:
            if npc_id not in self.npcs:
                self.npcs[npc_id] = {}
            if "psychology_log" not in self.npcs[npc_id]:
                self.npcs[npc_id]["psychology_log"] = []
            self.npcs[npc_id]["psychology_log"].append(entry)

    # ===== NPC 情景记忆（memory_log，与 psychology_log 并列，职责分离）=====

    def add_npc_memory(self, npc_id, entry):
        """追加一条NPC情景记忆（旧存档无memory_log字段则初始化）"""
        with NPCS_LOCK:
            if npc_id not in self.npcs:
                self.npcs[npc_id] = {}
            if not isinstance(self.npcs[npc_id].get("memory_log"), list):
                self.npcs[npc_id]["memory_log"] = []
            self.npcs[npc_id]["memory_log"].append(entry)

    def get_npc_memories_for_p1(self, npc_id, max_items=4):
        """选取注入P1的记忆：最近2条 + importance最高2条（去重，≤max_items条）"""
        with NPCS_LOCK:
            npc = self.npcs.get(npc_id) or {}
            log = list(npc.get("memory_log", [])) if isinstance(npc.get("memory_log"), list) else []
        return select_memories_for_p1(log, max_items)

    def get_npc_memory_count(self, npc_id):
        """获取NPC记忆条数（用于巩固触发判断）"""
        with NPCS_LOCK:
            npc = self.npcs.get(npc_id) or {}
            log = npc.get("memory_log")
            return len(log) if isinstance(log, list) else 0

    def replace_npc_memories(self, npc_id, new_log):
        """巩固后整体替换memory_log"""
        with NPCS_LOCK:
            if npc_id not in self.npcs:
                self.npcs[npc_id] = {}
            self.npcs[npc_id]["memory_log"] = list(new_log) if new_log else []

    def record_round(self, player_input, narrative, ai_output):
        """记录一轮交互"""
        self.current_round += 1
        entry = {
            "round": self.current_round,
            "input": player_input,
            "narrative": narrative,
            "ai_output": ai_output,
            "timestamp": None  # 可选
        }
        self.action_history.append(entry)

    def save_to_slot(self, target_name):
        """手动另存到指定槽位（2026-08-14）：把当前全部数据复制到目标槽位。
        目标槽位若已有存档会被覆盖（调用方负责确认）。返回 True/False"""
        try:
            from .save_manager import SaveManager, create_save_dir, save_json
            create_save_dir(target_name)
            target = SaveManager(target_name)
            # 世界设定 / 主角档案 / 设置（当前存档的静态数据）
            save_json(target.save_path, "world_template.json", self.world_template)
            save_json(target.save_path, "player_profile.json", self.player_profile)
            target.save_settings(self.settings)
            # 运行时数据
            target.save_player_state(self.player_state)
            target.save_known_facts(self.known_facts)
            target.save_action_history(self.action_history)
            target.save_story_threads(self.story_threads)
            target.save_map_data(self.map_data)
            for npc_id, data in list(self.npcs.items()):
                target.save_npc(npc_id, data)
            # 元信息（rounds/player_name + 本次手动保存时间）
            meta = dict(self.meta)
            meta["rounds"] = self.current_round
            meta["player_name"] = self.player_profile.get("name", "无名者")
            meta["last_played"] = datetime.now().isoformat()
            target.save_meta(meta)
            return True
        except Exception as e:
            print(f"[手动保存] 保存到 {target_name} 失败: {e}")
            return False

    def save_all(self):
        """保存所有运行时数据到存档"""
        # 玩家状态
        self.save_manager.save_player_state(self.player_state)

        # 已知事实
        self.save_manager.save_known_facts(self.known_facts)

        # 行动历史
        self.save_manager.save_action_history(self.action_history)

        # NPC更新（遍历用快照，防止异步任务并发修改）
        for npc_id, data in list(self.npcs.items()):
            self.save_manager.save_npc(npc_id, data)

        # 元信息
        self.save_manager.update_meta(
            rounds=self.current_round,
            player_name=self.player_profile.get("name", "无名者")
        )

        # 地图数据（P12地图师）
        self.save_map_data()

        # 检查分层存储触发（MVP简化：只归档，不生成摘要）
        trigger = self.config.get("game", "archive_trigger", default=250)
        if self.save_manager.check_archive_trigger(self.action_history, trigger):
            chunk = self.config.get("game", "archive_chunk", default=100)
            remaining, archived = self.save_manager.archive_old_history(self.action_history, chunk)
            if archived:
                self.action_history = remaining
                self.save_manager.save_action_history(self.action_history)
                print(f"[系统] 已归档旧记录到 {archived}")

        # 清空待处理
        self.pending_facts.clear()
        self.pending_npc_updates.clear()

        return True

    # ===== 回合回退快照（2026-08-14：撤销本回合，回到上一回合结束态）=====

    def snapshot_for_rollback(self):
        """保存当前完整状态为回退快照。调用时机：每回合真正开始处理前（P1调用前），
        此时内存=上一回合结束态（上一回合已save_all）。world_template/player_profile/settings 不变不回退。
        加锁：防止与P12(register_place)/P4C(巩固)后台线程并发修改 npcs/map_data 撕裂快照"""
        with NPCS_LOCK, MAP_LOCK, STATE_LOCK:
            self._rollback_snapshot = {
                "player_state": copy.deepcopy(self.player_state),
                "known_facts": copy.deepcopy(self.known_facts),
                "action_history": copy.deepcopy(self.action_history),
                "meta": copy.deepcopy(self.meta),
                "current_round": self.current_round,
                "npcs": copy.deepcopy(self.npcs),
                "story_threads": copy.deepcopy(self.story_threads),
                "map_data": copy.deepcopy(self.map_data),
                "pending_facts": copy.deepcopy(self.pending_facts),
                "pending_npc_updates": copy.deepcopy(self.pending_npc_updates),
            }

    def clear_rollback_snapshot(self):
        """清空回退快照（新提交时调用，保证快照只指向最近一回合开始前）"""
        self._rollback_snapshot = None

    def has_rollback_snapshot(self):
        """是否有可回退的上一回合快照"""
        return self._rollback_snapshot is not None

    def restore_rollback_snapshot(self):
        """回退：恢复快照 + save_all() 落盘。成功返回 True；无快照返回 False。
        世界设定/主角档案/设置不回退"""
        s = self._rollback_snapshot
        if not s:
            return False
        # 加锁整体替换，避免与后台线程交错写坏状态
        with NPCS_LOCK, MAP_LOCK, STATE_LOCK:
            self.player_state = copy.deepcopy(s["player_state"])
            self.known_facts = copy.deepcopy(s["known_facts"])
            self.action_history = copy.deepcopy(s["action_history"])
            self.meta = copy.deepcopy(s["meta"])
            self.current_round = s["current_round"]
            self.npcs = copy.deepcopy(s["npcs"])
            self.story_threads = copy.deepcopy(s["story_threads"])
            self.map_data = copy.deepcopy(s["map_data"])
            self.pending_facts = copy.deepcopy(s["pending_facts"])
            self.pending_npc_updates = copy.deepcopy(s["pending_npc_updates"])
        self.save_all()
        return True


# ===== P1 空间方位参考（2026-08-14 地图半封存：位置判断保留、图像封存，坐标转叙事锚点）=====
# 坐标系：+x=北、+y=东，1格=100米（docs/p12_map_design.md 第一节）

# 八方位索引：0起顺时针，与 atan2(dx, dy) 角度分档对齐
_ANCHOR_SECTORS = ["东", "东北", "北", "西北", "西", "西南", "南", "东南"]


def _direction_from_offset(dx, dy):
    """由相对偏移 (dx=北向, dy=东向) 映射八方位（纯函数）。
    atan2(dx, dy)：0°=正东、90°=正北、±180°=正西、-90°=正南；按45°分档"""
    if dx == 0 and dy == 0:
        return ""
    ang = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    return _ANCHOR_SECTORS[int((ang + 22.5) // 45) % 8]


def build_map_anchor_text(places, far_places, player_xy):
    """P1【空间方位参考】块文本：已上图地点相对玩家当前位置的方位（纯函数）。
    玩家所在地点标"你正位于此处"，其余地点按距离近→远输出"方向约 N 米"，
    far_places（有方位无坐标）输出"远方·方向"。
    玩家坐标不可解析（current_location 未上图）时返回空串，P1 不注入本块。
    参数：places={地名:{x,y,label,...}}，far_places=[{direction,name,label}]，player_xy=(x,y)或None"""
    if not player_xy or not isinstance(player_xy, (tuple, list)) or len(player_xy) != 2:
        return ""
    px, py = player_xy
    rows = []
    here = None
    offsets = []
    for name, info in (places or {}).items():
        if not isinstance(info, dict):
            continue
        try:
            lx, ly = int(info.get("x")), int(info.get("y"))
        except (TypeError, ValueError):
            continue
        label = str(info.get("label") or name or "")
        if (lx, ly) == (px, py):
            here = label
        else:
            offsets.append((label, lx - px, ly - py))
    if here:
        rows.append(f"- {here} → 你正位于此处")
    # 距离近→远（平方和排序，避免开方）
    offsets.sort(key=lambda o: o[1] * o[1] + o[2] * o[2])
    for label, dx, dy in offsets:
        direction = _direction_from_offset(dx, dy)
        dist_m = int(round((dx * dx + dy * dy) ** 0.5 * 100))
        rows.append(f"- {label} → {direction}约 {dist_m} 米")
    for p in (far_places or []):
        if not isinstance(p, dict):
            continue
        label = str(p.get("label") or p.get("name") or "")
        rows.append(f"- {label} → 远方·{p.get('direction', '')}")
    return "\n".join(rows)
