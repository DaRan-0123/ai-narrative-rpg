"""
无头表现层：把引擎的输出接进事件队列，供 HTTP 层取用。

GameEngine 通过一组「表现层方法」跟外界对话（append_system / _update_after_left …）。
界面版把它们画到 tkinter 上；这里把它们变成带序号的事件，服务层可以：

  · 轮询  GET /v1/saves/{name}/events?since=N
  · 或 SSE 每个事件到达即刻推送（_emit 会唤醒所有等待者）

引擎代码对此毫不知情——它仍然只是调 self.append_system(...)。

一个存档一个 HeadlessEngine（进程内缓存），因为引擎的内存状态
（game.player_state / npcs / map_data）就是要被续写的活状态。
"""
import threading
import time

from ..engine import GameEngine

# 事件日志上限：只保留最近这么多条。调用方断线太久会丢掉最旧的事件，
# 但存档本身是完整的，重连后从 /state 和 /history 补齐即可。
MAX_EVENTS = 2000


class TurnBusy(Exception):
    """同一存档已有回合/查询在跑"""


class HeadlessEngine(GameEngine):
    """把表现层实现成事件队列的游戏引擎。线程安全。"""

    def __init__(self, save_name):
        self._events = []
        self._seq = 0
        self._cond = threading.Condition()
        self._pending_risk = None
        self._turn_error = None
        self._assistant_answer = None
        self._turn_lock = threading.Lock()       # 同一存档同时只允许一个回合
        self._assistant_lock = threading.Lock()  # 助手查询独立于回合，但也不许并发
        super().__init__(save_name)

    # ==================== 事件流 ====================

    def _emit(self, type_, **payload):
        with self._cond:
            self._seq += 1
            event = {"seq": self._seq, "type": type_}
            event.update(payload)
            self._events.append(event)
            if len(self._events) > MAX_EVENTS:
                del self._events[:len(self._events) - MAX_EVENTS]
            self._cond.notify_all()
        return event

    def events_since(self, since=0):
        """取序号大于 since 的所有事件"""
        with self._cond:
            return [e for e in self._events if e["seq"] > since]

    def current_seq(self):
        with self._cond:
            return self._seq

    def wait_events(self, since, timeout):
        """阻塞直到有新事件或超时。返回 (events, 最新序号)"""
        with self._cond:
            if self._seq <= since:
                self._cond.wait(timeout)
            return [e for e in self._events if e["seq"] > since], self._seq

    def wait_quiet(self, ms, poll=0.1):
        """给后台任务（P3事实/P5世界/P11回顾/P12地图/记忆巩固）一点时间把事件吐出来。

        没动静且没有后台任务在跑就提前收工，不必死等满 ms。
        """
        deadline = time.monotonic() + ms / 1000.0
        last = self.current_seq()
        while time.monotonic() < deadline:
            time.sleep(poll)
            now = self.current_seq()
            if now == last and not self._background_running():
                break
            last = now

    def _background_running(self):
        """只看后台任务，不看本回合（_process_left 已由调用方 join 完毕）"""
        return bool(getattr(self, "_story_review_running", False)
                    or getattr(self, "_p12_running", False)
                    or getattr(self, "_p5_running", False)
                    or getattr(self, "_memory_consolidating", 0))

    # ==================== 表现层接口 ====================

    def _safe_after(self, ms, callback):
        """无 UI 线程可言，直接在当前线程执行。
        引擎只用 _safe_after(0, ...) 把结果交回表现层，同步执行语义等价且顺序确定。"""
        callback()

    def append_system(self, text, is_player=False):
        """系统助手栏。界面版会按 is_player / 方括号前缀打不同 tag，这里原样保留信息。"""
        self._emit("system", text=text, is_player=is_player)

    def _append_streamed_narrative(self, text):
        """P1 流式增量，一块一个事件"""
        self._emit("narrative_delta", text=text)

    def _show_error(self, message):
        self._turn_error = message
        self._processing_left = False
        self._emit("error", message=message)

    def _show_risk_confirm(self, user_input, reason):
        """P13 打回：记下待决动作，由 HTTP 层回给调用方去抉择。
        与界面版一致——打回期间放开入口（_set_left_busy(False)）"""
        self._pending_risk = {"action": user_input, "reason": reason}
        self._processing_left = False
        self._emit("risk", action=user_input, reason=reason)

    def _notify_background_issue(self, message):
        self._emit("background_issue", message=message)

    def _p12_report_error(self, message):
        self._emit("p12_error", message=message)

    def _update_after_left(self, narrative, player_state, skip_narrative=False):
        self._processing_left = False
        self._emit("turn_done", narrative=narrative, player_state=player_state,
                   round=self.game.current_round, skip_narrative=skip_narrative)

    def _update_after_right(self, answer):
        self._processing_right = False
        self._assistant_answer = answer
        self._emit("assistant", answer=answer)

    # ==================== 回合 ====================

    def run_turn(self, user_input, push_through=False, timeout=300, settle_ms=1500):
        """跑一个回合，阻塞到出结果。返回 dict，status 取值：

          ok              正常完成，narrative/round/player_state/events 齐全
          needs_decision  P13 打回，调用方决定后带 push_through=True 再发一次
          error           出错（含超时），error 字段有原因

        push_through=True 走 P14 双辩护人 → 裁判 P1 的裁定链路。
        """
        if not self._turn_lock.acquire(blocking=False):
            raise TurnBusy(self.save_name)

        try:
            self._pending_risk = None
            self._turn_error = None
            start = self.current_seq()

            self._processing_left = True
            if push_through:
                self._emit("player", text=user_input, push_through=True)
                self.append_system("[裁定中：双方分析员调查中...]")
                target, args = self._process_risky_input, (user_input,)
            else:
                # 与界面版 on_left_submit 一致：新提交先清旧快照，
                # 快照只在真正进入回合（P1 前）时才重新保存——风险打回时无快照可回退
                self.game.clear_rollback_snapshot()
                self._emit("player", text=user_input)
                self.append_system("[正在生成叙事...]")
                target, args = self._process_left_input, (user_input,)

            thread = threading.Thread(target=target, args=args, daemon=True)
            thread.start()
            thread.join(timeout)

            if thread.is_alive():
                self._processing_left = False
                return {"status": "error", "error": f"回合超时（{timeout}s），存档已自动保存到超时前状态"}

            if self._pending_risk:
                return {
                    "status": "needs_decision",
                    "action": self._pending_risk["action"],
                    "reason": self._pending_risk["reason"],
                    "options": ["push_through", "retry"],
                    "hint": "想继续就再发一次同样的输入并带 push_through=true；想换个做法就直接发新输入。",
                }
            if self._turn_error:
                return {"status": "error", "error": self._turn_error}

            # 回合主链路已完成。后台任务（P3/P5/P11/P12）还在跑，
            # 稍等一下把它们的事件一并收进来——但绝不为此阻塞太久。
            self.wait_quiet(settle_ms)

            events = self.events_since(start)
            narrative = next((e["narrative"] for e in events
                              if e["type"] == "turn_done"), "")
            return {
                "status": "ok",
                "round": self.game.current_round,
                "narrative": narrative,
                "player_state": self.game.player_state,
                "events": events,
            }
        finally:
            self._turn_lock.release()

    def run_assistant(self, query, timeout=120):
        """游戏助手查询（世界观/角色/关系），阻塞到出答案"""
        if not self._assistant_lock.acquire(blocking=False):
            raise TurnBusy(self.save_name + " (助手查询)")
        try:
            self._assistant_answer = None
            start = self.current_seq()
            self._processing_right = True
            self.append_system(query, is_player=True)
            self.append_system("[正在检索...]")

            thread = threading.Thread(target=self._process_right_input,
                                      args=(query,), daemon=True)
            thread.start()
            thread.join(timeout)

            if thread.is_alive():
                self._processing_right = False
                return {"status": "error", "error": f"查询超时（{timeout}s）"}

            return {
                "status": "ok",
                "answer": self._assistant_answer or "",
                "events": self.events_since(start),
            }
        finally:
            self._assistant_lock.release()

    def rollback(self):
        """撤销上一回合。返回 (ok, error)"""
        if self.is_processing():
            return False, "当前还有任务在处理中，请等待完成后再回退"
        if not self.game.has_rollback_snapshot():
            return False, "没有可回退的上一回合"
        if not self.game.restore_rollback_snapshot():
            return False, "回退失败：快照不可用"
        self._emit("rollback", round=self.game.current_round)
        return True, None

    def save(self):
        """手动保存"""
        self.game.save_all()
        self._emit("saved", round=self.game.current_round)

    # ==================== 状态快照 ====================

    def state_snapshot(self):
        """给调用方看的一份完整当前态。字段名与存档一致，不另起炉灶。"""
        ps = self.game.player_state
        return {
            "save": self.save_name,
            "round": self.game.current_round,
            "date": self.game.get_date_display(),
            "season": ps.get("game_season", ""),
            "time": ps.get("game_time", ""),
            "weather": ps.get("weather_environment", ""),
            "location": ps.get("current_location", ""),
            "health": ps.get("physical_health", ""),
            "items": ps.get("clothing_equipment", ""),
            "appearance": ps.get("appearance", ""),
            "people_in_scene": ps.get("current_scene_people", ""),
            "player_state": dict(ps),
            "player_profile": self.game.player_profile,
            "world_template": self.game.world_template,
            "settings": self.game.settings,
            "threads": self.game.get_active_threads(),
            "known_facts_count": len(self.game.known_facts),
            "npcs": {nid: {"name": n.get("name", ""),
                           "role": n.get("role", ""),
                           "alive": n.get("alive", True)}
                     for nid, n in self.game.npcs.items()},
            "map": self.game.map_data,
            "can_rollback": self.game.has_rollback_snapshot(),
            "busy": self.is_processing(),
        }

    def history(self, limit=20):
        """最近 limit 轮。叙事全文都在，调用方可自行渲染。"""
        entries = self.game.action_history[-limit:] if limit else self.game.action_history
        return [{"round": e.get("round"), "input": e.get("input", ""),
                 "narrative": e.get("narrative", "")} for e in entries]
