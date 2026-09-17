"""Language contract: the bilingual vocabulary shared by code and prompts.

Most of what the model returns is free text (narrative, dialogue, descriptions)
and never needs a fixed spelling. A handful of fields are different: the code
compares them, persists and re-matches them, or turns them into file paths.
Those fields need canonical tokens.

Canonical tokens are lowercase ASCII. Every normalizer here also accepts the
legacy Chinese spelling, so saves written before the English build keep loading
and old data is never silently reinterpreted.

Values that were already English (new_entities[].type, confidence, story-thread
status, and all JSON field names) are deliberately not listed here.
"""
import re

# ===== P5 world-change level =====
# Only world/region/society count as a world change; individual and none are
# filtered out by the caller.
WORLD_LEVELS = ("world", "region", "society")

_WORLD_LEVEL_ALIASES = {
    "world": "world", "world-level": "world", "世界级": "world",
    "region": "region", "region-level": "region", "区域级": "region",
    "society": "society", "society-level": "society", "社会级": "society",
}


def normalize_world_level(value):
    """Canonical world-change level, or "" when this is not a world-level change."""
    token = str(value or "").strip().lower().replace("_", "-").replace(" ", "-")
    return _WORLD_LEVEL_ALIASES.get(token, "")


# ===== P12 far direction =====
FAR_DIRECTIONS = ("north", "northeast", "east", "southeast",
                  "south", "southwest", "west", "northwest")

# Keyed by the separator-stripped, lowercased form, so "north-east",
# "North East" and "NE" all land on the same entry.
_FAR_ALIASES = {
    "north": "north", "n": "north", "北": "north",
    "northeast": "northeast", "ne": "northeast", "东北": "northeast",
    "east": "east", "e": "east", "东": "east",
    "southeast": "southeast", "se": "southeast", "东南": "southeast",
    "south": "south", "s": "south", "南": "south",
    "southwest": "southwest", "sw": "southwest", "西南": "southwest",
    "west": "west", "w": "west", "西": "west",
    "northwest": "northwest", "nw": "northwest", "西北": "northwest",
}


def normalize_far_direction(value):
    """Canonical compass direction, or "" when it is not one of the eight."""
    token = re.sub(r"[\s\-_]", "", str(value or "").strip().lower())
    return _FAR_ALIASES.get(token, "")


# ===== "nothing to report" sentinels =====
# Fields like season_sign and weather.reason use a placeholder when there is
# nothing to say. A bare truthiness test lets "none" through as a real value.
_ABSENT = {"", "none", "null", "nil", "n/a", "na", "无", "无变化", "无迹象", "无明显变化"}


def is_absent(value):
    """True when a model field means 'nothing to report' rather than a value."""
    return str(value or "").strip().lower().rstrip(".") in _ABSENT


# ===== "nobody else in the scene" sentinel =====
_ALONE = {"alone", "nobody", "no one", "no-one", "on my own", "独自一人"}


def is_alone(scene_people):
    """True when the scene has no other characters (or the field is empty)."""
    t = str(scene_people or "").strip().lower().rstrip(".")
    return (not t) or t in _ALONE


# ===== narrative style =====
# These five strings are simultaneously the wizard dropdown values, the value
# persisted in the save file, and the text injected into P1/P7. They are the
# canonical spelling; the Chinese names are the legacy ones.
NARRATIVE_STYLES = ("Grim Realism", "Poetic", "Plain", "Ornate", "Stream of Consciousness")

_STYLE_ALIASES = {
    "grim realism": "Grim Realism", "grimrealism": "Grim Realism",
    "hardboiled": "Grim Realism", "cold realism": "Grim Realism",
    "冷硬写实": "Grim Realism",
    "gritty写实": "Grim Realism",   # pre-2026-08-14 name, still in old saves
    "poetic": "Poetic", "poetic beauty": "Poetic", "诗意优美": "Poetic",
    "plain": "Plain", "plain and concise": "Plain", "spare": "Plain",
    "objective": "Plain", "客观简洁": "Plain",
    "ornate": "Ornate", "flowery": "Ornate", "literary": "Ornate",
    "华丽文学": "Ornate",
    "stream of consciousness": "Stream of Consciousness",
    "streamofconsciousness": "Stream of Consciousness",
    "consciousness": "Stream of Consciousness",
    "意识流": "Stream of Consciousness",
}


def normalize_style(value):
    """Canonical narrative style, or "" when the value is not one of the five."""
    token = re.sub(r"[\s\-_]", " ", str(value or "").strip().lower())
    return _STYLE_ALIASES.get(token, _STYLE_ALIASES.get(token.replace(" ", ""), ""))


# ===== fact.source / fact.category =====
# Not player-visible, but source is echoed into the P2 prompt ("（来源: …）")
# and both are matched by the P1 fact selector and the fact compactor.
SRC_WORLD_SEED = "world_seed"
SRC_PROTAGONIST_SEED = "protagonist_seed"
SRC_WORLD_EVENT = "world_event"
SRC_NARRATION = "narration"
SRC_EXPLORATION = "exploration"
SRC_ITEM = "item"
SRC_ENVIRONMENT = "environment"
SRC_COMPACTION = "compaction"

CAT_WORLDVIEW = "worldview"
CAT_LOCATION = "location"
CAT_PROTAGONIST = "protagonist"
CAT_NPC = "npc"

_LEGACY_SOURCES = {
    "世界初始化": SRC_WORLD_SEED,
    "主角创建": SRC_PROTAGONIST_SEED,
    "世界事件": SRC_WORLD_EVENT,
    "叙事": SRC_NARRATION,
    "探索发现": SRC_EXPLORATION,
    "获得物品": SRC_ITEM,
    "环境观察": SRC_ENVIRONMENT,
    "事实压缩": SRC_COMPACTION,
}

_LEGACY_CATEGORIES = {
    "世界观": CAT_WORLDVIEW,
    "地点": CAT_LOCATION,
    "主角": CAT_PROTAGONIST,
    "NPC": CAT_NPC,
}


def normalize_source(value):
    """Canonical fact source; unknown values pass through unchanged."""
    s = str(value or "").strip()
    return _LEGACY_SOURCES.get(s, s)


def normalize_category(value):
    """Canonical fact category; unknown values pass through unchanged."""
    c = str(value or "").strip()
    return _LEGACY_CATEGORIES.get(c, c)


def is_worldview_base(fact):
    """Facts that anchor the world and must never be dropped from the P1 injection.

    Matches on category first: that is where the world description and social
    framework live. The source check is kept for saves written before categories
    existed.
    """
    if not isinstance(fact, dict):
        return False
    if normalize_category(fact.get("category", "")) == CAT_WORLDVIEW:
        return True
    return "世界观" in str(fact.get("source", ""))


def is_world_event(fact):
    """True for P5 world-change facts, which are never compacted away."""
    if not isinstance(fact, dict):
        return False
    return normalize_source(fact.get("source", "")) == SRC_WORLD_EVENT
