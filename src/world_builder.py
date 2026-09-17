"""
世界创建：把「世界设定草稿」变成一份可玩的存档

链路：P9 多轮对话收集草稿 → P10 生成完整世界 → P6 完善初始NPC → P8 细化世界细节

界面版（src/ui/new_game_dialog.py）和服务版（src/server/）共用这一份实现。
不允许出现第二份——否则同一个草稿在两边会长出不同的世界。
"""
import json

from .api_client import call_main_json
from .game_state import GameState
from .vocab import NARRATIVE_STYLES, normalize_style
from .prompts import (P6_SYSTEM, build_p6_user, P8_SYSTEM, build_p8_user,
                      P9_SYSTEM, build_p9_user, P10_SYSTEM, build_p10_user)

# P9 草稿的初始键集合（只有已存在的键才会被新值覆盖，见 wizard_turn）
EMPTY_DRAFT = {
    "magic": None, "tech": None, "society": None, "economy": None,
    "order": None, "morality": None, "starting_area": None,
    "narrative_style": None, "assistant_tone": None, "perspective": None,
    "difficulty": None, "player_name": None, "player_appearance": None,
    "player_background": None, "world_vibe": None,
}


def wizard_turn(conversation_history, player_input, draft=None):
    """P9 世界创建向导一轮。

    无状态：对话历史与草稿都由调用方持有并传回，服务端不存任何会话。
    返回 dict：成功 {reply, world_draft, is_done, suggested_questions}
              失败 {error}
    """
    draft = dict(draft) if draft else dict(EMPTY_DRAFT)
    try:
        p9_user = build_p9_user(conversation_history, player_input)
        result, ok = call_main_json(P9_SYSTEM, p9_user, temperature=0.8, thinking=False)
        if not ok:
            return {"error": f"向导响应失败: {result.get('error', '未知错误')}"}

        # 只覆盖草稿里已有的键，且忽略空值——与界面版 _process_chat 的合并规则一致
        for key, value in (result.get("world_draft") or {}).items():
            if value and key in draft:
                draft[key] = value

        return {
            "reply": result.get("reply", "..."),
            "world_draft": draft,
            "is_done": bool(result.get("is_done", False)),
            "suggested_questions": result.get("suggested_questions", []),
            "raw": result,  # 界面版要把它整条存进对话历史（build_p9_user 从中取 reply）
        }
    except Exception as e:
        return {"error": f"处理出错: {e}"}


def build_world(world_draft, on_progress=None):
    """P10 → P6 → P8。

    on_progress(msg)：可选的进度回调（界面版用它往对话区插提示）
    返回 (world_data, player_info, settings, error)；error 非 None 时其余为 None
    """
    def _p(msg):
        if on_progress:
            on_progress(msg)

    try:
        _p("正在构建世界框架...")
        result, ok = call_main_json(P10_SYSTEM, build_p10_user(world_draft), temperature=0.8)
        if not ok:
            return None, None, None, f"世界生成失败: {result.get('error', '未知错误')}"

        world_options = result.get("world_options", {})
        # P10 是外部边界：narrative_style 可能为 null、或模型漏成中文/自创词，
        # 归一化成规范值再往下传（P7 提示词、settings、界面下拉框都用它）
        world_options["narrative_style"] = (normalize_style(world_options.get("narrative_style"))
                                            or NARRATIVE_STYLES[0])
        player_info = result.get("player_info", {})

        _p("正在设计角色...")
        result["initial_npcs"] = enhance_npcs(result, result.get("initial_npcs", []), player_info)

        _p("正在细化世界细节...")
        try:
            p8_user = build_p8_user(world_template=result, world_options=world_options,
                                    player_info=player_info)
            p8_result, p8_ok = call_main_json(P8_SYSTEM, p8_user, temperature=0.7, thinking=False)
            result["world_details"] = p8_result if (p8_ok and p8_result) else {}
        except Exception:
            result["world_details"] = {}

        settings = {
            "narrative_style": world_options.get("narrative_style", NARRATIVE_STYLES[0]),
            "assistant_tone": world_options.get("assistant_tone", "有人情味"),
            "perspective": world_options.get("perspective", "第二人称"),
            "difficulty": world_options.get("difficulty", "中立"),
            "history_limit": 10,
        }
        return result, player_info, settings, None

    except Exception as e:
        return None, None, None, f"生成失败: {e}"


def create_save(save_name, world_draft, on_progress=None):
    """跑完整条链并落盘。返回 (save_name, error)"""
    world_data, player_info, settings, err = build_world(world_draft, on_progress)
    if err:
        return None, err
    if on_progress:
        on_progress("正在保存世界...")
    try:
        GameState(save_name).init_new(world_data, player_info, settings)
    except Exception as e:
        return None, f"保存失败: {e}"
    if on_progress:
        on_progress("世界创建完成！")
    return save_name, None


def enhance_npcs(result, initial_npcs, player_info):
    """调用P6完善初始NPC（P6失败或异常时降级为只用 entity 自带字段）"""
    enhanced = []
    for i, entity in enumerate(initial_npcs):
        if isinstance(entity, dict) and entity.get("type") == "npc":
            fallback = {
                **entity,
                "npc_id": f"npc_{i + 1:03d}",
                "role": entity.get("description", "未知身份")[:30],
                "appearance": entity.get("description", ""),
                "personality": "未知",
                "background": "未知",
                "relationship_to_player": "未知",
                "psychology_log": ["初始状态"],
            }
            try:
                p6_user = build_p6_user(
                    world_template={
                        "world_description": result.get("world_description", ""),
                        "social_framework": result.get("social_framework", ""),
                    },
                    existing_npcs={},
                    current_narrative=result.get("initial_situation", ""),
                    new_entity_description=entity,
                    player_state=result.get("initial_player_state", {}),
                    round_num=0,
                )
                p6_result, p6_ok = call_main_json(P6_SYSTEM, p6_user, temperature=0.7, thinking=False)
                if p6_ok and p6_result:
                    merged = {**entity, **p6_result}
                    merged["npc_id"] = f"npc_{i + 1:03d}"
                    enhanced.append(merged)
                else:
                    enhanced.append(fallback)
            except Exception:
                enhanced.append(fallback)
        else:
            enhanced.append(entity)
    return enhanced
