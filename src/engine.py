"""
游戏引擎（无界面）

2026-09-17 从 src/ui/game_window.py 原样抽出，为的是让「界面版」和「服务版」
共用同一份回合编排逻辑——不允许出现第二份实现，否则两边必然走样。

引擎不认识 tkinter。它通过下面「表现层接口」里的一组方法跟外界对话，
由子类提供实现：
    src/ui/game_window.py  GameWindow      → 画到界面上
    src/server/headless.py HeadlessEngine  → 写进事件队列，供 HTTP 层取用

引擎方法是从 game_window.py 逐行搬过来的，逻辑一字未改；唯一新增的是
__init__（原来混在 GameWindow.__init__ 里，含建控件部分）与接口声明。
"""
import json
import re
import threading

from .game_state import GameState, MEMORY_CONSOLIDATE_THRESHOLD, parse_time_passed
from .api_client import (
    call_main, call_main_json, call_lightweight, call_main_stream,
)
from .prompts import (
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
from .vocab import (
    normalize_far_direction, normalize_world_level,
    is_absent, is_worldview_base, is_world_event,
    SRC_EXPLORATION, SRC_ITEM, SRC_ENVIRONMENT, SRC_WORLD_EVENT, SRC_COMPACTION,
)

# P12远方方位枚举在 src/vocab.py（校验用，见 docs/p12_map_design.md 第五节）
P12_GRID_LIMIT = 10  # 坐标合法范围 [-10, 10]

# 游戏助手查询类型判定关键字（英文小写；中文保留以兼容中文输入）
RELATIONSHIP_KEYWORDS = (
    "关系", "人际", "朋友", "敌人", "好感", "信任", "认识谁",
    "和谁", "关系网", "社交", "人脉",
    "relationship", "relationships", "friend", "friends", "enemy", "enemies",
    "ally", "allies", "trust", "rapport", "attitude", "who do i know",
    "social", "contacts", "network",
)


class NarrativeExtractor:
    """P1流式输出时，从增量 JSON 文本中实时提取 narrative 字段字符串值。
    用法：循环 feed(delta)（feed 返回增量 narrative 文本，可空），或逐块收集后取 result。
    实现：正则定位 `"narrative"\s*:\s*"`（只匹配该键，转义后的 \\"narrative\\" 不匹配），
    进入字符串值收集直到未转义 `"` 闭合；未闭合时对累积 raw 尝试 json.loads 前缀反解，
    与已显示部分 diff 后输出增量——天然处理 \\n / \\" / \\uXXXX 转义与多字节切分。
    narrative 字段闭合后（_done=True）后续 feed 不再输出。"""

    _KEY_RE = re.compile(r'"narrative"\s*:\s*"')

    def __init__(self):
        self._buf = ""
        self._in_narr = False      # 已定位到 narrative 值起始
        self._done = False         # narrative 值已闭合
        self._raw = ""             # narrative 原始字符（含转义）累积
        self._shown = ""           # 已解码输出的 narrative 文本
        self._esc = False          # 上一个字符是否为反斜杠（转义中）

    def feed(self, delta):
        """喂入一段文本增量，返回 narrative 新增文本（未开始/已结束返回空串）。
        每次 feed 消费掉当前缓冲（已并入 raw），避免重复处理；尾部若为未完成转义
        的反斜杠则保留其转义态到下次 feed。"""
        if not delta:
            return ""
        if self._done:
            return ""
        self._buf += delta
        out = ""
        # 尚未定位键 → 搜
        if not self._in_narr:
            m = self._KEY_RE.search(self._buf)
            if m:
                self._in_narr = True
                self._raw = ""
                self._esc = False
                # 丢弃键之前内容，值起始留在 buf，下次循环处理
                self._buf = self._buf[m.end():]
            else:
                # 键可能跨块被切：只保留末尾足够长的一段（> 键+冒号引号总长）
                keep = 24
                if len(self._buf) > keep:
                    self._buf = self._buf[-keep:]
                return ""
        # 在 narrative 字符串内：逐字符消费，检查闭合
        buf = self._buf
        n = len(buf)
        i = 0
        while i < n:
            ch = buf[i]
            if self._esc:
                self._esc = False
                self._raw += ch
            elif ch == "\\":
                self._esc = True
                self._raw += ch
            elif ch == '"':
                # 值闭合：结束引号不入 raw（解码时由外层包裹的引号充当闭合）
                self._done = True
                break
            else:
                self._raw += ch
            i += 1
        # 已消费 i+1 个字符（闭合时含闭合引号；未闭合时到 buf 末尾）
        if self._done:
            self._buf = buf[i + 1:]
        else:
            # 全部并入 raw；若 _esc 仍 True（buf 以反斜杠结尾），保留转义态，raw 已含该反斜杠
            self._buf = ""
        # 解码已累积 raw（未闭合时 json.loads 可能失败，跳过等下次）
        try:
            decoded = json.loads('"' + self._raw + '"')
        except Exception:
            decoded = None
        if decoded is not None and len(decoded) > len(self._shown):
            out = decoded[len(self._shown):]
            self._shown = decoded
        return out

    @property
    def result(self):
        """完整 narrative 文本（未闭合时返回已解码部分）"""
        return self._shown


class GameEngine:
    """回合编排引擎。子类必须实现「表现层接口」里列出的方法。"""

    def __init__(self, save_name):
        self.save_name = save_name

        # 处理中标志（防双重提交、防处理中离开窗口）
        self._processing_left = False
        self._processing_right = False
        self._story_review_running = False
        # P12地图师运行标志（2026-08-14地图半封存：保留位置判断，图像已封存）
        self._p12_running = False
        # P5世界变化运行标志（2026-08-15：P1的world_event非空时后台触发，防重入）
        self._p5_running = False
        # P13风险门：被打回待确认的风险动作（确认条显示期间保存，抉择后清空）
        self._risk_action = None
        # P1叙事流式（2026-08-14）：是否流式生成中、首块已显示标记
        self._streaming_active = False
        self._stream_first_shown = False
        # 回合回退（2026-08-14）：本回合叙事区起始标记（回退时删到此处）
        self._round_mark = None

        # 加载游戏状态
        self.game = GameState(save_name)
        self.game.load()

    # ==================== 表现层接口（子类实现）====================
    # 引擎只调这些方法跟外界对话。基类一律抛 NotImplementedError，
    # 漏实现会立刻炸出来，不会静默变成哑巴。

    def _safe_after(self, ms, callback):
        """把回调排到表现层线程执行（GUI 用 root.after；无头版直接执行/入队）"""
        raise NotImplementedError

    def append_system(self, text, is_player=False):
        """追加一行到「游戏助手」栏（系统通知/后台任务结果）"""
        raise NotImplementedError

    def _append_streamed_narrative(self, text):
        """P1 流式叙事增量到达"""
        raise NotImplementedError

    def _show_error(self, message):
        """回合出错时的表现层反馈"""
        raise NotImplementedError

    def _show_risk_confirm(self, user_input, reason):
        """P13 风险门打回：把「要不要放手一搏」交给玩家"""
        raise NotImplementedError

    def _notify_background_issue(self, message):
        """后台任务失败提示（不中断游戏）"""
        raise NotImplementedError

    def _p12_report_error(self, message):
        """P12 地图师出错提示"""
        raise NotImplementedError

    def _update_after_left(self, narrative, player_state, skip_narrative=False):
        """主对话回合完成后的表现层收尾（显示叙事、刷状态栏、放开入口）"""
        raise NotImplementedError

    def _update_after_right(self, answer):
        """游戏助手查询完成后的表现层收尾"""
        raise NotImplementedError

    # ==================== 引擎本体 ====================

    def is_processing(self):
        """是否有正在进行的回合/查询/后台任务（供表现层判定能否回退/离开）"""
        return (self._processing_left or self._processing_right
                or getattr(self, "_story_review_running", False)
                or getattr(self, "_p12_running", False)
                or getattr(self, "_memory_consolidating", 0) > 0)

    def _process_left_input(self, user_input):
        """后台处理主对话输入（正常路径；有风险的动作在P13打回确认，见_run_risk_gate）"""
        try:
            npcs_in_scene = self.game.get_npcs_in_scene()
            settings = self.game.settings

            p1_user = build_p1_user(
                world_template=self.game.world_template,
                player_state=self.game.player_state,
                action_history=self.game.action_history,
                known_facts_summary=self.game.get_known_facts_summary(limit=40),
                npcs_context=npcs_in_scene,
                player_input=user_input,
                settings=settings,
                round_num=self.game.current_round + 1,
                world_details=self.game.world_template.get("world_details"),
                story_threads=self.game.get_active_threads(),
                map_anchor_text=self._build_map_anchor_text()
            )

            # ===== P13 风险门（2026-08-05 用户定案裁定链路）=====
            # 有风险 → 打回确认（后台线程就此打住，等玩家抉择）；无风险 → 继续正常链路
            risk, p13_reason = self._run_risk_gate(user_input)
            if risk:
                self._safe_after(0, lambda: self._show_risk_confirm(user_input, p13_reason))
                return

            # 保存回退快照：此时内存=上一回合结束态，P1开始生成后本回合即可被撤销（2026-08-14）
            self.game.snapshot_for_rollback()

            # P1 叙事主引擎（流式：narrative 实时显示，关闭思考模式）
            result, ok = self._stream_p1(P1_SYSTEM, p1_user, 0.7)

            self._handle_p1_result(result, ok, user_input, narrative_already_shown=True)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._safe_after(0, lambda: self._show_error(f"处理出错: {str(e)}"))

    def _handle_p1_result(self, result, ok, user_input, narrative_already_shown=False):
        """P1返回后的统一处理（正常链路/裁判链路共用）。
        内容与原_process_left_input的P1调用后处理完全一致，仅提取为方法供两条链路复用。
        narrative_already_shown=True：P1流式时叙事已实时显示，_update_after_left 跳过重复 append"""
        try:
            if not ok:
                self._safe_after(0, lambda: self._show_error(f"叙事生成失败: {result.get('error', '未知错误')}"))
                return

            # 验证状态文本完整性（P1可能输出null，统一兜底为空dict）
            player_state = result.get("player_state") or {}
            missing_fields = self._check_state_fields(player_state)

            if missing_fields:
                retry_prompt = f"""你上一轮的状态JSON中，以下字段为空：{', '.join(missing_fields)}。
请基于当前叙事，补全这些字段的值。注意：每个字段必须有内容，不能留空。

当前叙事：{(result.get('narrative') or '')[:500]}"""
                retry_result, retry_ok = call_main_json(P1_SYSTEM, retry_prompt, temperature=0.5, thinking=False)
                if retry_ok:
                    for field in missing_fields:
                        if field in retry_result and retry_result[field]:
                            player_state[field] = retry_result[field]
                        else:
                            player_state[field] = self._default_state_value(field)
                else:
                    for field in missing_fields:
                        player_state[field] = self._default_state_value(field)

            narrative = result.get("narrative") or "[Narrative generation failed]"

            # 更新游戏状态
            self.game.update_player_state(player_state)
            # 季节由游戏内日历决定，覆盖P1的自由判断（见 docs/season_weather_design.md）
            self.game.player_state["game_season"] = self.game.get_season()
            self.game.record_round(user_input, narrative, result)

            # 处理facts_delta（P1可能输出null，兜底为空列表）
            facts_delta = result.get("facts_delta") or []
            for fact in facts_delta:
                if isinstance(fact, dict):
                    self.game.add_fact(fact)

            # P5世界质变（2026-08-15）：P1的world_event非空 → 后台触发P5深化（零延迟，不阻塞回合）
            world_event = result.get("world_event")
            if world_event and not getattr(self, "_p5_running", False):
                self._p5_running = True
                threading.Thread(target=self._maybe_run_p5,
                                 args=(world_event, narrative), daemon=True).start()

            # known_facts 压缩（2026-08-15）：超长时后台合并旧低置信事实，控 token 成本
            if len(self.game.known_facts) > 100 and not getattr(self, "_facts_compact_running", False):
                self._facts_compact_running = True
                threading.Thread(target=self._maybe_compact_facts, daemon=True).start()

            # 处理new_entities（新NPC/地点/物品）
            new_entities = result.get("new_entities")
            if new_entities:
                # === 去重：过滤掉已存在的NPC ===
                existing_npc_names = {npc.get("name", "").lower() for npc in list(self.game.npcs.values())}
                filtered_entities = []
                for entity in new_entities:
                    if isinstance(entity, dict) and entity.get("type") == "npc":
                        name = entity.get("name", "").lower()
                        if name and name not in existing_npc_names:
                            filtered_entities.append(entity)
                            existing_npc_names.add(name)  # 防止同一轮内重复
                        elif name in existing_npc_names:
                            # 已存在，跳过不生成
                            pass
                    else:
                        filtered_entities.append(entity)

                # === 并发调用P6处理所有新NPC ===
                npc_entities = [e for e in filtered_entities if isinstance(e, dict) and e.get("type") == "npc"]
                non_npc_entities = [e for e in filtered_entities if not (isinstance(e, dict) and e.get("type") == "npc")]

                import concurrent.futures

                def _call_p6_for_entity(entity):
                    """为单个NPC调用P6"""
                    try:
                        p6_user = build_p6_user(
                            world_template=self.game.world_template,
                            existing_npcs=self.game.npcs,
                            current_narrative=narrative,
                            new_entity_description=entity,
                            player_state=self.game.player_state,
                            round_num=self.game.current_round
                        )
                        p6_result, p6_ok = call_main_json(
                            P6_SYSTEM, p6_user, temperature=0.7, thinking=False
                        )
                        if p6_ok and p6_result:
                            merged_npc = {**entity, **p6_result}
                            return merged_npc
                        else:
                            # P6失败，回退
                            entity["role"] = entity.get("description", "Unknown role")[:60]
                            entity["appearance"] = entity.get("description", "")
                            entity["personality"] = "Unknown"
                            entity["background"] = "Unknown"
                            entity["relationship_to_player"] = "Unknown"
                            entity["psychology_log"] = ["Initial state"]
                            return entity
                    except Exception:
                        # 异常回退
                        entity["role"] = entity.get("description", "Unknown role")[:60]
                        entity["appearance"] = entity.get("description", "")
                        entity["personality"] = "Unknown"
                        entity["background"] = "Unknown"
                        entity["relationship_to_player"] = "Unknown"
                        entity["psychology_log"] = ["Initial state"]
                        return entity

                # 并发执行所有P6调用
                if npc_entities:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                        future_to_entity = {
                            executor.submit(_call_p6_for_entity, entity): entity
                            for entity in npc_entities
                        }
                        for future in concurrent.futures.as_completed(future_to_entity):
                            merged_npc = future.result()
                            npc_id = self.game.add_npc_from_entity(merged_npc)

                # 处理非NPC实体（地点/物品）
                for entity in non_npc_entities:
                    if isinstance(entity, dict):
                        entity_type = entity.get("type")
                        if entity_type == "location":
                            self.game.add_fact({
                                "content": f"Location: {entity.get('name', 'Unknown')} - {entity.get('description', '')}",
                                "source": SRC_EXPLORATION
                            })
                        elif entity_type == "item":
                            self.game.add_fact({
                                "content": f"Item: {entity.get('name', 'Unknown')} - {entity.get('description', '')}",
                                "source": SRC_ITEM
                            })

            # 处理npc_notes -> 调用P4进行深度心理分析（P1可能输出null，兜底为空dict）
            npc_notes = result.get("npc_notes") or {}
            p4_updated_npcs = []  # 本轮P4处理过的NPC，供记忆巩固检查
            for npc_id, note in npc_notes.items():
                if note and npc_id in self.game.npcs:
                    try:
                        p4_user = build_p4_user(
                            npc_data=self.game.npcs[npc_id],
                            event_description=str(note)
                        )
                        p4_result, p4_ok = call_main_json(
                            P4_SYSTEM, p4_user, temperature=0.7, thinking=False
                        )
                        if p4_ok:
                            self.game.add_npc_psychology(npc_id, {
                                "round": self.game.current_round,
                                "event": f"Round {self.game.current_round} interaction",
                                "p4_analysis": p4_result
                            })
                            # 写入情景记忆（memory_entry为null则跳过，向后兼容旧输出）
                            memory_entry = p4_result.get("memory_entry")
                            if isinstance(memory_entry, dict) and memory_entry.get("event"):
                                self._add_memory_from_p4(npc_id, memory_entry)
                        else:
                            self.game.add_npc_psychology(npc_id, {
                                "round": self.game.current_round,
                                "event": f"Round {self.game.current_round} interaction",
                                "impact": str(note)
                            })
                        p4_updated_npcs.append(npc_id)
                    except Exception:
                        self.game.add_npc_psychology(npc_id, {
                            "round": self.game.current_round,
                            "event": f"Round {self.game.current_round} interaction",
                            "impact": str(note)
                        })

            # 记忆巩固检查：超过阈值时异步触发（flash模型，不阻塞回合）
            for npc_id in p4_updated_npcs:
                if self.game.get_npc_memory_count(npc_id) > MEMORY_CONSOLIDATE_THRESHOLD:
                    self._memory_consolidating = getattr(self, "_memory_consolidating", 0) + 1
                    threading.Thread(
                        target=self._consolidate_npc_memory,
                        args=(npc_id,),
                        daemon=True
                    ).start()

            # 并行调用轻量模型（P3事实提取）
            self._call_p3_async(narrative)

            # 剧情线回顾检查（P11故事师，异步线程不阻塞回合；重复触发用运行标志防护）
            if self.game.should_run_story_review() and not getattr(self, "_story_review_running", False):
                self._story_review_running = True
                threading.Thread(target=self._run_story_review, daemon=True).start()

            # P12地图师：程序门——current_location未注册才触发（异步线程不阻塞回合）
            self._maybe_run_p12(narrative)

            # 自动保存
            self.game.save_all()

            # UI更新（流式时叙事已实时显示，skip 避免重复 append）
            self._safe_after(0, lambda: self._update_after_left(
                narrative, player_state, skip_narrative=narrative_already_shown))

        except Exception as e:
            import traceback
            traceback.print_exc()
            err_msg = f"处理出错: {str(e)}"
            self._safe_after(0, lambda: self._show_error(err_msg))

    def _stream_p1(self, system, user, temperature=0.7):
        """P1流式调用（后台线程内调用，2026-08-14）：
        边收边用 NarrativeExtractor 提取 narrative 并节流实时显示到主叙事窗口，
        收完整后用与 call_main_json 相同的逻辑解析 JSON（失败重试一次）。
        返回 (result_dict, ok)"""
        import time as _time
        full_text = ""
        extractor = NarrativeExtractor()
        pending = []
        last_flush = [_time.time()]
        self._streaming_active = True
        self._stream_first_shown = False

        def _flush():
            if pending:
                text = "".join(pending)
                pending.clear()
                self._safe_after(0, lambda t=text: self._append_streamed_narrative(t))

        err = None
        for kind, data in call_main_stream(system, user, temperature=temperature, thinking=False):
            if kind == "error":
                err = data
                break
            full_text += data
            piece = extractor.feed(data)
            if piece:
                pending.append(piece)
                now = _time.time()
                if now - last_flush[0] >= 0.08:   # ~80ms 节流，避免每 token 一次 Tk 调度
                    last_flush[0] = now
                    _flush()
        _flush()  # 收尾剩余
        self._streaming_active = False

        if err:
            return {"error": err}, False
        if not full_text.strip():
            return {"error": "空响应"}, False

        # 解析完整 JSON（与 call_main_json 相同：markdown 清理 + json.loads，失败重试一次）
        text = full_text.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        try:
            return json.loads(text), True
        except json.JSONDecodeError as e:
            retry_prompt = f"""请只返回纯JSON，不要包含任何markdown代码块标记（如 ```json）。
之前的响应解析失败，错误: {e}

请重新输出以下内容的JSON格式：
{text[:1000]}
"""
            return call_main_json(system, retry_prompt, temperature, thinking=False)

    def _run_risk_gate(self, user_input):
        """P13风险门（后台线程内调用）。返回 (risk: bool, reason: str)。
        调用失败/解析失败一律视为无风险放行（判定层故障不阻断游戏）"""
        try:
            raw, ok = call_lightweight(
                P13_SYSTEM, build_p13_user(user_input, self.game.player_state),
                temperature=0.1)
            if not ok:
                print(f"[风险门] P13调用失败，按无风险放行: {raw}")
                return False, ""
            import json as _json
            import re as _re
            text = raw.strip()
            if text.startswith("```"):
                text = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
            data = _json.loads(text)
            if not isinstance(data, dict) or "risk" not in data:
                print(f"[风险门] P13输出格式异常，按无风险放行: {text[:100]}")
                return False, ""
            risk = bool(data.get("risk"))
            reason = str(data.get("reason", ""))
            print(f"[风险门] risk={risk} | {reason}")
            return risk, reason
        except Exception as e:
            print(f"[风险门] P13异常，按无风险放行: {e}")
            return False, ""

    def _process_risky_input(self, user_input):
        """放手一搏的裁定链路（后台线程）：
        P14双辩护人（顺序2次flash）→ 裁判P1（注入优势/劣势清单）→ 统一处理。
        P14失败一方items视为空；两方都失败则退回正常P1链路。"""
        try:
            npcs_in_scene = self.game.get_npcs_in_scene()
            facts = self.game.get_known_facts_summary(limit=40)

            reports = {}
            for side in ("advocate", "opponent"):
                try:
                    sys_p, usr_p = build_p14_prompts(
                        side, user_input, self.game.player_state,
                        npcs_in_scene, facts)
                    raw, ok = call_lightweight(sys_p, usr_p, temperature=0.3)
                    if not ok:
                        print(f"[裁定] P14-{side}调用失败，该方清单为空: {raw}")
                        reports[side] = []
                        continue
                    import json as _json
                    import re as _re
                    text = raw.strip()
                    if text.startswith("```"):
                        text = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
                    data = _json.loads(text)
                    items = data.get("items") if isinstance(data, dict) else None
                    reports[side] = [str(i) for i in items if i] if isinstance(items, list) else []
                    print(f"[裁定] P14-{side} {len(reports[side])}条")
                except Exception as e:
                    print(f"[裁定] P14-{side}异常，该方清单为空: {e}")
                    reports[side] = []

            # p1_user组装参数与正常链路完全一致（仅来源分叉：裁判块注入）
            p1_user = build_p1_user(
                world_template=self.game.world_template,
                player_state=self.game.player_state,
                action_history=self.game.action_history,
                known_facts_summary=facts,
                npcs_context=npcs_in_scene,
                player_input=user_input,
                settings=self.game.settings,
                round_num=self.game.current_round + 1,
                world_details=self.game.world_template.get("world_details"),
                story_threads=self.game.get_active_threads(),
                map_anchor_text=self._build_map_anchor_text()
            )
            if not reports["advocate"] and not reports["opponent"]:
                print("[裁定] P14双方均失败，退回正常P1链路")
                final_user = p1_user
            else:
                final_user = build_p1_judge_user(
                    p1_user, reports["advocate"], reports["opponent"])

            # 保存回退快照（与正常链路一致：进入裁判P1前保存上一回合结束态）
            self.game.snapshot_for_rollback()

            # 裁判P1（流式显示，处理与正常链路完全一致）
            result, ok = self._stream_p1(P1_SYSTEM, final_user, 0.7)
            self._handle_p1_result(result, ok, user_input, narrative_already_shown=True)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._safe_after(0, lambda: self._show_error(f"处理出错: {str(e)}"))

    def _check_state_fields(self, state):
        """检查状态文本字段是否完整"""
        required = ["current_location", "posture_action", "clothing_equipment",
                    "physical_health", "transportation", "weather_environment", "current_scene_people"]
        missing = []
        for field in required:
            if field not in state or not state[field]:
                missing.append(field)
        return missing

    def _default_state_value(self, field):
        """缺失字段的默认值"""
        defaults = {
            "current_location": "Unknown location",
            "posture_action": "standing",
            "clothing_equipment": "plain clothes",
            "physical_health": "healthy",
            "transportation": "none (on foot)",
            "weather_environment": "clear",
            "current_scene_people": "alone"
        }
        return defaults.get(field, "Unknown")

    def _call_p3_async(self, narrative):
        """异步调用P3事实提取（含时间/天气/季节迹象，见 docs/season_weather_design.md）"""
        try:
            current_weather = self.game.player_state.get("weather_environment", "")
            p3_user = build_p3_user(narrative, current_weather=current_weather)
            result, ok = call_lightweight(P3_SYSTEM, p3_user, temperature=0.3)
            if not ok:
                # 修复：后台任务失败也要让用户可见，不再静默吞掉
                self._safe_after(0, lambda: self._notify_background_issue("P3事实提取失败"))
                return
            import json
            import re
            text = result.strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
            data = json.loads(text)

            # 向后兼容：旧格式是纯facts数组，新格式是含facts/time_passed/weather的对象
            if isinstance(data, list):
                facts, extra = data, {}
            elif isinstance(data, dict):
                facts = data.get("facts") or []
                extra = data
            else:
                return
            for f in facts:
                if isinstance(f, dict) and "content" in f:
                    self.game.add_fact(f)

            # 游戏时间累加（缺失/解析失败按0.25天兜底，保证日历持续走动）
            self.game.add_game_days(parse_time_passed(extra.get("time_passed")))

            # game_time 联动日历（2026-08-15）：用累加后的 game_day 小数推算时段，覆盖P1自由填写
            self.game.player_state["game_time"] = self.game.get_game_time()

            # 天气写回：叙事有明确依据（changed=true）才允许变化
            weather = extra.get("weather")
            if isinstance(weather, dict) and weather.get("changed") and weather.get("current"):
                self.game.player_state["weather_environment"] = str(weather["current"])

            # 季节迹象只记录为普通事实，不改变日历驱动的季节（v1简化）
            sign = extra.get("season_sign")
            if isinstance(sign, str) and not is_absent(sign):
                self.game.add_fact({"content": f"Seasonal sign: {sign.strip()}", "source": SRC_ENVIRONMENT})
        except Exception as e:
            # 失败原因必须打出来：只说"提取失败"的话，限流、断网、解析错、
            # 提示词超长都长一个样，排查时等于没有线索
            print(f"[P3] 事实提取失败: {e!r}")
            # 修复：P3后台失败时提示用户（不中断主流程）
            self._safe_after(0, lambda: self._notify_background_issue("P3事实提取失败"))

    def _maybe_run_p5(self, world_event, narrative):
        """P5世界质变判定与世界观改写（后台线程，不阻塞回合收尾）。
        P1的world_event非空才调用（P1已判断本轮有质变）；个人级/无变化不写不改。
        质变时：写入known_facts + 改写world_template.world_description并落盘 + 系统助手提示"""
        try:
            p5_user = build_p5_user(self.game.world_template, narrative)
            raw, ok = call_lightweight(P5_SYSTEM, p5_user, temperature=0.3)
            if not ok:
                self._safe_after(0, lambda: self._notify_background_issue("世界变化判定失败"))
                return
            import json as _json
            import re as _re
            text = raw.strip()
            if text.startswith("```"):
                text = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
            try:
                data = _json.loads(text)
            except Exception:
                return
            if not isinstance(data, dict) or not data.get("world_changed"):
                return  # 无质变
            if not normalize_world_level(data.get("change_level", "")):
                return  # 个人级/无变化/其他不算世界质变
            desc = str(data.get("change_description") or "").strip()
            if not desc:
                return

            # 1) 写入已知事实
            self.game.add_fact({"content": f"World event: {desc}", "source": SRC_WORLD_EVENT})
            # 2) 改写世界观（world_description）并落盘
            new_world = str(data.get("updated_world_description") or "").strip()
            if new_world and self.game.world_template.get("world_description") != new_world:
                self.game.world_template["world_description"] = new_world
                from .save_manager import save_json
                save_json(self.game.save_manager.save_path, "world_template.json", self.game.world_template)
            # 3) 系统助手提示玩家
            self._safe_after(0, lambda d=desc: self.append_system(f"【世界】{d}"))
        except Exception as e:
            # 对玩家静默（不中断游戏），但原因要留在日志里
            print(f"[P5] 世界变化判定失败: {e!r}")
        finally:
            self._p5_running = False

    def _maybe_compact_facts(self):
        """facts > 100 条时，把最旧的 30 条低置信/普通事实合并成 2-3 条概述（后台线程）。
        原事实完整移入 archives/facts_archive.json（不删除，只移出活跃集）。
        世界观基盘、世界事件、high 置信保留在活跃集；压缩后 50 轮内不重复。失败静默"""
        try:
            if len(self.game.known_facts) <= 100:
                return
            last = getattr(self, "_facts_compact_round", 0)
            if self.game.current_round - last < 50:
                return
            # 选最旧的、非世界观/世界事件、低置信的事实（取 30 条）
            old = []
            for f in self.game.known_facts:
                if len(old) >= 30:
                    break
                if not isinstance(f, dict):
                    continue
                if is_worldview_base(f) or is_world_event(f):
                    continue
                if f.get("confidence") == "high":
                    continue
                old.append(f)
            if len(old) < 10:
                return  # 可压缩的太少
            old_text = "\n".join(f"- {f.get('content','')}" for f in old)
            prompt = (f"下面是一批零散的游戏事实，请把它们合并压缩为 2-3 条更概括的事实。\n"
                      f"【铁律】概括必须覆盖原事实中的全部关键信息，绝不允许丢失任何设定、人物、地点、事件；\n"
                      f"宁多勿漏，如有把握不准的就原样保留到概述里。\n"
                      f"只输出JSON数组，每个元素是字符串：\n{old_text}")
            raw, ok = call_lightweight("你是一位信息整理员，负责把零散事实压缩为概述，必须完整不丢信息。", prompt, temperature=0.3)
            if not ok:
                return
            import json as _json
            import re as _re
            text = raw.strip()
            if text.startswith("```"):
                text = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
            try:
                summaries = _json.loads(text)
            except Exception:
                return
            if not isinstance(summaries, list) or not summaries:
                return
            # 1) 旧事实完整写入归档（绝不删除，可恢复）
            from .save_manager import save_json, load_json
            archive_path = self.game.save_manager.save_path / "archives" / "facts_archive.json"
            archive = load_json(archive_path, "facts_archive.json", [])
            if not isinstance(archive, list):
                archive = []
            archive.extend(old)
            save_json(self.game.save_manager.save_path / "archives", "facts_archive.json", archive)
            # 2) 从活跃集移出旧事实（仅移出，数据已归档）
            old_ids = {id(f) for f in old}
            self.game.known_facts = [f for f in self.game.known_facts if id(f) not in old_ids]
            # 3) 概述加入活跃集（保留信息入口）
            added = 0
            for s in summaries[:3]:
                if isinstance(s, str) and s.strip():
                    self.game.add_fact({"content": s.strip(), "source": SRC_COMPACTION, "confidence": "medium"})
                    added += 1
            self._facts_compact_round = self.game.current_round
            print(f"[压缩] 已把 {len(old)} 条旧事实归档（完整保留）并合并为 {added} 条概述")
        except Exception:
            pass  # 压缩失败静默，不中断游戏
        finally:
            self._facts_compact_running = False

    def _add_memory_from_p4(self, npc_id, memory_entry):
        """把P4返回的memory_entry规范化为记忆条目并写入memory_log"""
        try:
            importance = int(memory_entry.get("importance", 1))
        except (TypeError, ValueError):
            importance = 1
        importance = max(1, min(5, importance))
        tags = memory_entry.get("tags")
        if not isinstance(tags, list):
            tags = [str(tags)] if tags else []
        entry = {
            "round": self.game.current_round,
            "event": str(memory_entry.get("event", "")),
            "perception": str(memory_entry.get("perception", "")),
            "importance": importance,
            "tags": [str(t) for t in tags],
        }
        self.game.add_npc_memory(npc_id, entry)

    @staticmethod
    def _split_for_consolidation(memory_log, keep_recent=5):
        """拆分memory_log：(重要记忆, 待合并的旧琐碎记忆, 保留的新琐碎记忆)
        importance>=3 全部保留；importance<=2 中仅最近keep_recent条保留，更早的参与合并"""
        important = [m for m in memory_log if (m.get("importance", 1) or 1) >= 3]
        trivial = [m for m in memory_log if (m.get("importance", 1) or 1) <= 2]
        old_trivial = trivial[:-keep_recent] if len(trivial) > keep_recent else []
        recent_trivial = trivial[-keep_recent:] if trivial else []
        return important, old_trivial, recent_trivial

    @classmethod
    def _build_consolidated_log(cls, memory_log, summary_entry):
        """巩固拼接（纯逻辑，便于测试）：
        新memory_log = 保留的重要记忆 + 1条概述 + 未参与合并的新记忆
        返回 (new_log, merged_count)；无可合并记忆时返回 (原log, 0)"""
        important, old_trivial, recent_trivial = cls._split_for_consolidation(memory_log)
        if not old_trivial:
            return memory_log, 0
        new_log = important + [summary_entry] + recent_trivial
        return new_log, len(old_trivial)

    def _consolidate_npc_memory(self, npc_id):
        """后台线程：NPC记忆巩固（flash模型），失败只记日志不阻塞回合"""
        try:
            # 加锁读取 + 校验：防同NPC并发巩固读到旧log整体replace丢更新
            from .game_state import NPCS_LOCK
            with NPCS_LOCK:
                npc = self.game.npcs.get(npc_id)
                if not npc:
                    return
                log = npc.get("memory_log")
                if not isinstance(log, list) or len(log) <= MEMORY_CONSOLIDATE_THRESHOLD:
                    return
                log = list(log)  # 快照，后续在锁外基于此计算
            _important, old_trivial, _recent = self._split_for_consolidation(log)
            if not old_trivial:
                print(f"[记忆] {npc.get('name', npc_id)} 记忆超阈值但无可合并的低重要性旧记忆，跳过巩固")
                return

            p4c_user = build_p4c_user(npc.get("name", npc_id), old_trivial)
            raw, ok = call_lightweight(P4C_SYSTEM, p4c_user, temperature=0.3)
            if not ok:
                print(f"[记忆] {npc.get('name', npc_id)} 巩固调用失败: {raw}")
                # 修复：后台巩固失败也提示用户
                self._safe_after(0, lambda: self._notify_background_issue("记忆巩固失败"))
                return

            # 解析flash返回的概述记忆（兼容markdown代码块）
            import json as _json
            import re as _re
            text = raw.strip()
            if text.startswith("```"):
                text = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
            summary = _json.loads(text)
            if not isinstance(summary, dict) or not summary.get("event"):
                print(f"[记忆] {npc.get('name', npc_id)} 巩固返回格式异常，跳过")
                return

            # 强制规范化为"时期概述"条目（importance=2, tags=["概述"]）
            summary_entry = {
                "round": self.game.current_round,
                "event": str(summary.get("event", "")),
                "perception": str(summary.get("perception", "")),
                "importance": 2,
                "tags": ["overview"],
            }
            new_log, merged = self._build_consolidated_log(log, summary_entry)
            self.game.replace_npc_memories(npc_id, new_log)
            # 立即持久化该NPC档案（不等下一轮save_all）
            self.game.save_manager.save_npc(npc_id, self.game.npcs[npc_id])
            print(f"[记忆] {npc.get('name', npc_id)} 记忆巩固完成：合并{merged}条旧记忆 → 1条概述，现共{len(new_log)}条")
        except Exception as e:
            print(f"[记忆] 记忆巩固失败({npc_id}): {e}")
            # 修复：异常时提示用户（不中断主流程）
            self._safe_after(0, lambda: self._notify_background_issue("记忆巩固失败"))
        finally:
            # 复位巩固运行计数（is_processing 据此判断是否处理中）
            n = getattr(self, "_memory_consolidating", 0)
            if n > 0:
                self._memory_consolidating = n - 1

    def _run_story_review(self):
        """后台线程：P11剧情线回顾（pro模型），失败只记日志不阻塞回合"""
        try:
            game = self.game

            # 最近10轮历史压缩成摘要（每轮一行：轮次+玩家输入+叙事前80字）
            recent = game.get_recent_history(10)
            history_summary = ""
            for entry in recent:
                history_summary += (f"Round {entry.get('round', '?')}: {entry.get('input', '')}"
                                    f" → {entry.get('narrative', '')[:80]}\n")
            if not history_summary:
                history_summary = "(no history yet)\n"

            # 全场NPC伏笔汇总（plot_hooks + 秘密）
            npc_hooks = ""
            for npc_id, npc in list(game.npcs.items()):
                name = npc.get("name", npc_id)
                hooks = npc.get("plot_hooks") or []
                secrets = npc.get("secrets") or []
                if hooks or secrets:
                    npc_hooks += f"【{name}】\n"
                    for h in hooks:
                        npc_hooks += f"  - Hook: {h}\n"
                    for s in secrets:
                        npc_hooks += f"  - Secret: {s}\n"
            if not npc_hooks:
                npc_hooks = "(none)\n"

            # 最近30条已知事实
            facts_summary = game.get_known_facts_summary()
            recent_facts = "\n".join(f"- {f}" for f in facts_summary[-30:]) or "(none)"

            p11_user = build_p11_user(
                existing_threads=game.get_story_threads(),
                history_summary=history_summary,
                npc_hooks=npc_hooks,
                recent_facts=recent_facts,
                world_description=game.world_template.get("world_description", ""),
                round_num=game.current_round,
            )
            result, ok = call_main_json(P11_SYSTEM, p11_user, temperature=0.7, thinking=False)
            if not ok or not isinstance(result, dict):
                print(f"[剧情线] P11调用失败: {result}")
                # 修复：P11失败提示用户
                self._safe_after(0, lambda: self._notify_background_issue("剧情回顾失败"))
                return
            new_threads = result.get("threads")
            if not isinstance(new_threads, list):
                print(f"[剧情线] P11返回格式异常: {str(result)[:200]}")
                return

            game.update_threads(new_threads)
            game.save_manager.save_story_threads(game.story_threads)
            game.save_manager.update_meta(last_story_review_round=game.current_round)
            # 同步内存中的meta，避免下轮重复触发（update_meta只写磁盘）
            game.meta["last_story_review_round"] = game.current_round
            active = [t for t in game.story_threads["threads"] if t.get("status") == "active"]
            print(f"[剧情线] P11回顾完成：共{len(game.story_threads['threads'])}条（active {len(active)}条）")
        except Exception as e:
            print(f"[剧情线] P11回顾失败: {e}")
            # 修复：异常时提示用户
            self._safe_after(0, lambda: self._notify_background_issue("剧情回顾失败"))
        finally:
            self._story_review_running = False

    def _maybe_run_p12(self, narrative):
        """程序门（docs/p12_map_design.md 第九节）：current_location名字未注册才触发P12"""
        location = str(self.game.player_state.get("current_location", "")).strip()
        if not location:
            return
        if self.game.is_location_registered(location):
            return
        if self._p12_running:
            return  # 上一次P12还在跑，下轮再试（不重入）
        self._p12_running = True
        threading.Thread(target=self._run_p12, args=(location, narrative),
                         daemon=True).start()

    def _validate_p12_result(self, result, location_name):
        """P12输出校验清单（docs/p12_map_design.md 第七节，每条对应真实踩坑）。
        通过返回 (kind, payload)：kind ∈ none/far/coord；失败抛 ValueError（不重试，直接报错——用户定案2）"""
        if not isinstance(result, dict):
            raise ValueError(f"输出不是JSON对象: {str(result)[:100]}")
        # 装傻校验：name必须原样回显待安置地点名（实验发现flash偶尔谎称未提供输入并甩far跑路）
        if result.get("name") != location_name:
            raise ValueError(f"name回声不符：期望「{location_name}」，实得「{result.get('name')}」")
        # none：主角未身临其境，正常结果不落库
        if result.get("none") is True:
            return ("none", result.get("reason", ""))
        # far：远方方位
        if "far" in result:
            far = normalize_far_direction(result["far"])
            if not far:
                raise ValueError(f"far值非法：「{result['far']}」不在八方位枚举内")
            return ("far", {"direction": far, "label": self._p12_short_label(result, location_name)})
        # 坐标：整数 + 不越界 + 不撞格 + icon_subject非空
        x, y = result.get("x"), result.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            raise ValueError(f"坐标非整数: x={x!r}, y={y!r}")
        if not (-P12_GRID_LIMIT <= x <= P12_GRID_LIMIT and -P12_GRID_LIMIT <= y <= P12_GRID_LIMIT):
            raise ValueError(f"坐标越界: ({x},{y}) 不在 [-{P12_GRID_LIMIT},{P12_GRID_LIMIT}] 内")
        occupied = {(p["x"], p["y"]) for p in self.game.get_map_places().values()}
        if (x, y) in occupied:
            raise ValueError(f"坐标碰撞: ({x},{y}) 已被已有地点占用")
        icon_subject = str(result.get("icon_subject") or "").strip()
        if not icon_subject:
            raise ValueError("坐标输出缺少icon_subject（图标管线断料）")
        return ("coord", {"x": x, "y": y, "icon_subject": icon_subject,
                          "label": self._p12_short_label(result, location_name),
                          "reason": result.get("reason", "")})

    @staticmethod
    def _p12_short_label(result, location_name):
        """显示/叙事用短名：优先P12给的label（建议4个英文单词内，2026-08-04 用户定）；
        缺失时退回兜底——取地名最后一个逗号分段"""
        label = str(result.get("label") or "").strip()
        if label:
            if len(label.split()) > 4:
                print(f"[地图] 提示：label「{label}」超过4个单词（建议值，不拦截）")
            return label
        import re as _re
        parts = [p for p in _re.split(r"[，,、]", location_name) if p]
        # strip：英文地名分段是「Old Town, The Harbor Inn」，不 strip 会留下前导空格
        return (parts[-1].strip() if parts else location_name)[:24]

    def _run_p12(self, location_name, narrative):
        """后台线程：P12定位 → 校验 → 落库（2026-08-14地图半封存：图标生成/渲染已封存）"""
        try:
            # 组装输入：已上图地点清单 + 叙事片段 + 当前位置
            places = self.game.get_map_places()
            existing = [{"name": n, "x": p["x"], "y": p["y"],
                         "desc": p.get("icon_subject", "")} for n, p in places.items()]
            p12_user = build_p12_user(
                existing_places=existing,
                snippet=narrative,
                current_location=location_name,
                target_location=location_name,
            )
            raw, ok = call_lightweight(P12_SYSTEM, p12_user, temperature=0.3)
            if not ok:
                print(f"[地图] P12调用失败: {raw}")
                self._safe_after(0, lambda: self._notify_background_issue("地图定位失败"))
                return
            # 解析JSON（兼容markdown代码块）
            import json as _json
            import re as _re
            text = raw.strip()
            if text.startswith("```"):
                text = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
            try:
                result = _json.loads(text)
            except Exception as e:
                self._p12_report_error(f"JSON解析失败: {e}，原文: {text[:100]}")
                return

            # 校验清单（失败直接报错记录日志，不自动重试——用户定案2）
            try:
                kind, payload = self._validate_p12_result(result, location_name)
            except ValueError as ve:
                self._p12_report_error(f"P12校验失败: {ve}")
                return

            if kind == "none":
                # P12门：主角未身临其境（正常结果，非错误，不落库不打扰用户）
                print(f"[地图] P12判断「{location_name}」仅被提及未到达: {payload}")
                return

            if kind == "far":
                self.game.register_far_place(location_name, payload["direction"], label=payload["label"])
                self.game.save_map_data()
                print(f"[地图] 远方地点落库: {payload['direction']} · {location_name}")
                self._safe_after(0, lambda n=location_name: self.append_system(
                    f"[地图] 远方之地已标记：{n}"))
                return

            # kind == "coord"：落库即锁定（一生只定位一次）
            x, y, icon_subject = payload["x"], payload["y"], payload["icon_subject"]
            self.game.register_place(location_name, x, y, icon_subject, label=payload["label"])
            self.game.save_map_data()
            print(f"[地图] 新地点落库: {location_name} → ({x},{y})，理由: {payload['reason']}")
            self._safe_after(0, lambda n=location_name: self.append_system(
                f"[地图] 新地点已定位：{n}"))
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[地图] P12处理失败: {e}")
            self._safe_after(0, lambda: self._notify_background_issue("地图定位失败"))
        finally:
            self._p12_running = False

    def _build_map_anchor_text(self):
        """P1【空间方位参考】块文本：已上图地点相对玩家当前位置的方位（助力叙事）。
        玩家当前位置未上图时返回空串，build_p1_user 不注入本块"""
        from .game_state import build_map_anchor_text as _fmt
        return _fmt(self.game.get_map_places(), self.game.get_far_places(),
                    self.game.get_player_xy())

    def _process_right_input(self, query):
        """后台处理游戏助手查询（增强版：支持人际关系查询）"""
        try:
            # 判断查询类型
            query_lower = query.lower()
            is_relationship_query = any(kw in query_lower for kw in RELATIONSHIP_KEYWORDS)

            recent_history = self.game.get_recent_history(5)

            if is_relationship_query:
                # 构建人际关系上下文
                relationships = []
                for npc_id, npc in list(self.game.npcs.items()):
                    name = npc.get("name", npc_id)
                    rel = npc.get("relationship_to_player", "Unknown")
                    role = npc.get("role", "")
                    logs = npc.get("psychology_log", [])
                    recent_log = logs[-1] if logs else ""
                    relationships.append(
                        f"- {name} ({role}): relationship to player is {rel}."
                        f"Latest state of mind: {recent_log}"
                    )

                rel_context = "[Player relationships]\n" + "\n".join(relationships) if relationships else "No records."

                p2_user = f"""{build_p2_user(
                    known_facts=self.game.known_facts,
                    action_history_recent=recent_history,
                    player_query=query,
                    player_state=self.game.player_state
                )}

{rel_context}

【查询类型】人际关系/社交关系
请基于上述人际关系信息回答玩家的问题。如果信息不足，请说明。"""
            else:
                p2_user = build_p2_user(
                    known_facts=self.game.known_facts,
                    action_history_recent=recent_history,
                    player_query=query,
                    player_state=self.game.player_state
                )

            result, ok = call_main(P2_SYSTEM, p2_user, temperature=0.3)

            if not ok:
                answer = f"查询失败: {result}"
            else:
                answer = result

            self._safe_after(0, lambda: self._update_after_right(answer))

        except Exception as e:
            err_msg = f"查询出错: {str(e)}"
            self._safe_after(0, lambda: self._update_after_right(err_msg))
