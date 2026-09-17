"""
存档管理模块
负责存档的创建、读取、保存、列表查询
"""
import json
import os
import gzip
import re
import shutil
import sys
import threading
from pathlib import Path
from datetime import datetime

from .vocab import (SRC_WORLD_SEED, SRC_PROTAGONIST_SEED, CAT_WORLDVIEW,
                    CAT_LOCATION, CAT_PROTAGONIST, CAT_NPC)

if getattr(sys, "frozen", False):
    # 打包成 exe：存档放 exe 同级的 saves/。
    # 不能用 __file__——onefile 模式下它指向临时解压目录，存档会随退出消失
    SAVES_DIR = Path(sys.executable).parent / "saves"
else:
    SAVES_DIR = Path(__file__).parent.parent / "saves"

# 写盘锁：防止主循环save_all与异步任务（P4C记忆巩固/P11剧情回顾）交叉写坏JSON
# 注意：只在文件IO期间持有，绝不在持锁时调用API（见 docs 锁定顺序约定）
SAVE_LOCK = threading.RLock()


def ensure_saves_dir():
    SAVES_DIR.mkdir(parents=True, exist_ok=True)


def _is_backup_dir(name):
    """备份/临时目录不进存档列表：含 _backup/_polluted/_tmp 标记"""
    return any(m in name for m in ("_backup", "_polluted", "_tmp"))


def _normalize_last_played(raw):
    """把 last_played 归一化为 '%Y-%m-%d %H:%M:%S'（精确到秒）。
    兼容 isoformat（含T和微秒）与 'YYYY-MM-DD HH:MM[:SS]' 两种来源；解析失败返回 None"""
    if not raw:
        return None
    try:
        # isoformat: 2026-08-14T21:32:45.123456
        if "T" in raw:
            return datetime.fromisoformat(raw).strftime("%Y-%m-%d %H:%M:%S")
        # 'YYYY-MM-DD HH:MM:SS' 或 'YYYY-MM-DD HH:MM'（无秒补 :00）
        s = raw.strip()
        if len(s) == 16:
            s += ":00"
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def list_saves():
    """返回所有存档的列表"""
    ensure_saves_dir()
    saves = []
    for entry in sorted(SAVES_DIR.iterdir()):
        if entry.is_dir() and not _is_backup_dir(entry.name):
            meta_file = entry / "meta.json"
            if meta_file.exists():
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except:
                    meta = {}
            else:
                meta = {}

            # 获取最后修改时间（last_played 精确到秒；meta 存的是 isoformat，解析为 %Y-%m-%d %H:%M:%S）
            mtime = entry.stat().st_mtime
            raw_lp = meta.get("last_played", "")
            lp = _normalize_last_played(raw_lp)
            if lp is None:
                lp = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            saves.append({
                "name": entry.name,
                "path": str(entry),
                "last_played": lp,
                "rounds": meta.get("rounds", 0),
                "exists": True
            })

    # 补齐到10个槽位
    existing_names = {s["name"] for s in saves}
    for i in range(1, 11):
        slot_name = f"存档{i}"
        if slot_name not in existing_names:
            saves.append({
                "name": slot_name,
                "path": str(SAVES_DIR / slot_name),
                "last_played": "空",
                "rounds": 0,
                "exists": False
            })

    # 存档槽位按数字自然排序（"存档2" < "存档10"），非"存档N"名字（如"老陈的旅途"）排在最前
    def _slot_key(s):
        m = re.match(r"^存档(\d+)$", s["name"])
        return (0, int(m.group(1))) if m else (1, 0, s["name"])
    return sorted(saves, key=_slot_key)


def create_save_dir(save_name):
    """创建存档目录结构"""
    ensure_saves_dir()
    save_path = SAVES_DIR / save_name
    save_path.mkdir(parents=True, exist_ok=True)
    (save_path / "npcs").mkdir(exist_ok=True)
    (save_path / "locations").mkdir(exist_ok=True)
    (save_path / "archives").mkdir(exist_ok=True)
    return save_path


def save_json(save_path, filename, data):
    """保存JSON文件到存档目录（原子写：先写临时文件再替换，全程持写盘锁）"""
    filepath = Path(save_path) / filename
    tmp_path = Path(save_path) / (filename + ".tmp")
    with SAVE_LOCK:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, filepath)


def load_json(save_path, filename, default=None):
    """从存档目录读取JSON文件"""
    filepath = Path(save_path) / filename
    if not filepath.exists():
        return default if default is not None else {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"failed to read {filename}: {e}")
        return default if default is not None else {}


def delete_save(save_name):
    """删除存档"""
    save_path = SAVES_DIR / save_name
    if save_path.exists():
        shutil.rmtree(save_path)
        return True
    return False


class SaveManager:
    """存档管理器，封装对一个具体存档的所有操作"""

    def __init__(self, save_name):
        self.save_path = SAVES_DIR / save_name
        self.name = save_name

    def init_new_save(self, world_data, player_info, settings):
        """初始化新存档，写入所有基础文件"""
        create_save_dir(self.name)

        # 世界模板
        save_json(self.save_path, "world_template.json", {
            "world_description": world_data.get("world_description", ""),
            "social_framework": world_data.get("social_framework", ""),
            "starting_area_description": world_data.get("starting_area_description", ""),
            "world_details": world_data.get("world_details", {})
        })

        # 玩家档案
        save_json(self.save_path, "player_profile.json", player_info)

        # 玩家状态
        initial_state = world_data.get("initial_player_state", {
            "current_location": "Unknown location",
            "posture_action": "standing",
            "clothing_equipment": "plain clothes",
            "physical_health": "healthy",
            "transportation": "none (on foot)",
            "weather_environment": "clear",
            "current_scene_people": "alone"
        })
        save_json(self.save_path, "player_state.json", initial_state)

        # 已知事实（初始信息库：世界观+主角+NPC同步写入）
        initial_facts = []
        
        # 世界观事实
        world_desc = world_data.get("world_description", "")
        if world_desc:
            initial_facts.append({
                "content": f"Worldview: {world_desc[:600]}",
                "source": SRC_WORLD_SEED,
                "category": CAT_WORLDVIEW
            })
        
        social_fw = world_data.get("social_framework", "")
        if social_fw:
            initial_facts.append({
                "content": f"Social framework: {social_fw[:600]}",
                "source": SRC_WORLD_SEED,
                "category": CAT_WORLDVIEW
            })
        
        area_desc = world_data.get("starting_area_description", "")
        if area_desc:
            initial_facts.append({
                "content": f"Starting area: {area_desc[:600]}",
                "source": SRC_WORLD_SEED,
                "category": CAT_LOCATION
            })
        
        # 主角事实
        player_name = player_info.get("name", "Unnamed")
        player_appearance = player_info.get("appearance", "")
        player_background = player_info.get("background", "")
        initial_facts.append({
            "content": f"Player name: {player_name}",
            "source": SRC_PROTAGONIST_SEED,
            "category": CAT_PROTAGONIST
        })
        if player_appearance:
            initial_facts.append({
                "content": f"Player appearance: {player_appearance}",
                "source": SRC_PROTAGONIST_SEED,
                "category": CAT_PROTAGONIST
            })
        if player_background:
            initial_facts.append({
                "content": f"Player background: {player_background}",
                "source": SRC_PROTAGONIST_SEED,
                "category": CAT_PROTAGONIST
            })
        
        # NPC事实
        for npc in world_data.get("initial_npcs", []):
            npc_name = npc.get("name", "Unknown")
            npc_role = npc.get("role", "")
            npc_background = npc.get("background", "")
            npc_rel = npc.get("relationship_to_player", "Unknown")
            fact_content = f"NPC {npc_name}"
            if npc_role:
                fact_content += f", role: {npc_role}"
            if npc_background:
                fact_content += f", background: {npc_background[:300]}"
            fact_content += f", relationship to player: {npc_rel}"
            initial_facts.append({
                "content": fact_content,
                "source": SRC_WORLD_SEED,
                "category": CAT_NPC
            })
        
        # 初始位置
        init_loc = initial_state.get("current_location", "Unknown location")
        initial_facts.append({
            "content": f"Current location: {init_loc}",
            "source": SRC_WORLD_SEED,
            "category": CAT_LOCATION
        })
        
        save_json(self.save_path, "known_facts.json", initial_facts)

        # 行动历史
        save_json(self.save_path, "action_history.json", [])

        # 设置
        save_json(self.save_path, "settings.json", settings)

        # NPC档案
        for npc in world_data.get("initial_npcs", []):
            npc_id = npc.get("npc_id", f"npc_{npc.get('name', 'unknown')}")
            save_json(self.save_path / "npcs", f"{npc_id}.json", npc)

        # 元信息
        save_json(self.save_path, "meta.json", {
            "created_at": datetime.now().isoformat(),
            "last_played": datetime.now().isoformat(),
            "rounds": 0,
            "player_name": player_info.get("name", "Unnamed")
        })

        return True

    def load_world_template(self):
        return load_json(self.save_path, "world_template.json", {})

    def load_player_profile(self):
        return load_json(self.save_path, "player_profile.json", {})

    def load_player_state(self):
        return load_json(self.save_path, "player_state.json", {})

    def load_known_facts(self):
        return load_json(self.save_path, "known_facts.json", [])

    def load_action_history(self):
        return load_json(self.save_path, "action_history.json", [])

    def load_last_n_rounds(self, n=3):
        """加载最近n轮行动记录，用于存档加载时恢复显示"""
        history = self.load_action_history()
        return history[-n:] if len(history) >= n else history

    def load_settings(self):
        return load_json(self.save_path, "settings.json", {})

    def load_story_threads(self):
        """加载剧情线（P11故事师维护；旧存档无此文件时返回空结构）"""
        return load_json(self.save_path, "story_threads.json", {"threads": []})

    def save_story_threads(self, data):
        """保存剧情线"""
        save_json(self.save_path, "story_threads.json", data)

    def load_map_data(self):
        """加载地图数据（P12地图师维护；旧存档无此文件时返回空结构，向后兼容）"""
        return load_json(self.save_path, "map_data.json",
                         {"places": {}, "far_places": [], "version": 0})

    def save_map_data(self, data):
        """保存地图数据"""
        save_json(self.save_path, "map_data.json", data)

    def load_npc(self, npc_id):
        return load_json(self.save_path / "npcs", f"{npc_id}.json", {})

    def load_all_npcs(self):
        """加载所有NPC"""
        npcs = {}
        npc_dir = self.save_path / "npcs"
        if npc_dir.exists():
            for f in npc_dir.glob("*.json"):
                npc_id = f.stem
                npcs[npc_id] = load_json(npc_dir, f.name, {})
        return npcs

    def save_player_state(self, state):
        save_json(self.save_path, "player_state.json", state)

    def save_known_facts(self, facts):
        save_json(self.save_path, "known_facts.json", facts)

    def save_action_history(self, history):
        save_json(self.save_path, "action_history.json", history)

    def save_npc(self, npc_id, data):
        save_json(self.save_path / "npcs", f"{npc_id}.json", data)

    def save_settings(self, settings):
        save_json(self.save_path, "settings.json", settings)

    def load_meta(self):
        return load_json(self.save_path, "meta.json", {})

    def save_meta(self, meta):
        save_json(self.save_path, "meta.json", meta)

    def update_meta(self, **kwargs):
        # read-modify-write 整体在锁内完成，防止与异步任务的update_meta互相覆盖
        with SAVE_LOCK:
            meta = self.load_meta()
            meta.update(kwargs)
            meta["last_played"] = datetime.now().isoformat()
            self.save_meta(meta)

    # ===== 分层存储（MVP简化版，预留接口）=====

    def check_archive_trigger(self, history, trigger=250):
        """检查是否需要归档"""
        return len(history) >= trigger

    def archive_old_history(self, history, chunk_size=100):
        """
        将最旧的 chunk_size 轮归档到压缩文件
        返回: (剩余历史, 归档的文件名)
        """
        if len(history) <= chunk_size:
            return history, None

        to_archive = history[:chunk_size]
        remaining = history[chunk_size:]

        # 生成归档文件名（round 缺失/非整数时兜底，防 TypeError）
        def _round(v, default):
            try:
                r = int(v.get("round", default) or default)
                return r
            except (TypeError, ValueError):
                return default
        start_round = _round(to_archive[0], 1)
        end_round = _round(to_archive[-1], chunk_size)
        filename = f"action_history_{start_round:04d}_{end_round:04d}.json.gz"
        filepath = self.save_path / "archives" / filename

        # 压缩保存
        data = json.dumps(to_archive, ensure_ascii=False)
        with gzip.open(filepath, "wt", encoding="utf-8") as f:
            f.write(data)

        return remaining, filename
