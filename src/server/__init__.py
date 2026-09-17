"""
服务版：把游戏的后台跑成一个 HTTP 服务，不要界面。

给别人把这个世界接进自己的程序里用——网页、机器人、Discord Bot、
自己的客户端，都行。协议就是 JSON over HTTP，没有别的东西要学。

只用 Python 标准库（http.server + json），不引入任何新依赖。

跑起来：
    python serve.py --port 8765

详细端点说明见 docs/API.md。
"""
import hmac
import json
import re
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

from .headless import HeadlessEngine, TurnBusy
from ..world_builder import EMPTY_DRAFT, create_save, wizard_turn
from ..save_manager import delete_save, list_saves

# ==================== 引擎注册表 ====================
# 一个存档一个引擎实例：引擎的内存状态就是被续写的活状态，不能每次请求重建。

_engines = {}
_engines_lock = threading.Lock()


def get_engine(save_name):
    with _engines_lock:
        engine = _engines.get(save_name)
        if engine is None:
            engine = HeadlessEngine(save_name)
            _engines[save_name] = engine
        return engine


def drop_engine(save_name):
    with _engines_lock:
        _engines.pop(save_name, None)


def _save_exists(save_name):
    return any(s["exists"] and s["name"] == save_name for s in list_saves())


# 存档名会变成磁盘上的目录名，必须挡住路径穿越
_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _check_name(name):
    if not name or not isinstance(name, str):
        return "save name must not be empty"
    if _BAD_NAME.search(name) or name in (".", "..") or name.strip() != name:
        return "save name contains invalid characters (cannot contain \\ / : * ? \" < > | or leading/trailing spaces)"
    if len(name) > 40:
        return "save name is too long (max 40 characters)"
    return None


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AI-RPG-Service/1.0"

    # ---------- 基础 ----------

    def log_message(self, fmt, *args):
        print(f"[service] {self.address_string()} {fmt % args}")

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ApiError(400, f"invalid JSON in request body: {e}")
        if not isinstance(data, dict):
            raise ApiError(400, "request body must be a JSON object")
        return data

    def _wants_stream(self, query, body):
        if (self.headers.get("Accept") or "").find("text/event-stream") >= 0:
            return True
        return bool(body.get("stream")) or query.get("stream", ["0"])[0] in ("1", "true")

    def _check_auth(self):
        token = self.server.api_token
        if not token:
            return
        got = self.headers.get("Authorization") or ""
        # HTTP 头按 latin-1 解码，非 ASCII 的 token 到这儿已经是乱码，永远比不中。
        # serve() 启动时就会拦住这种 token，这里只做常数时间比较。
        if not hmac.compare_digest(got, f"Bearer {token}"):
            raise ApiError(401, "missing or invalid Authorization: Bearer <token>")

    # ---------- SSE（HTTP/1.1 分块传输）----------

    def _sse_start(self):
        self._streaming = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _sse_raw(self, payload):
        """一个 HTTP/1.1 分块。长度必须按 payload 实际字节数算——
        写死长度会让后面的分块全部错位，客户端读到一半就断。"""
        self.wfile.write(b"%x\r\n" % len(payload) + payload + b"\r\n")
        self.wfile.flush()

    def _sse_send(self, obj):
        self._sse_raw(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))

    def _sse_ping(self):
        self._sse_raw(b": ping\n\n")   # SSE 注释行，客户端忽略

    def _sse_end(self):
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    # ---------- 路由 ----------

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        # 响应头是否已经发出。SSE 一旦开始，后面再出错也不能补发一个 JSON 响应——
        # 那等于往同一条流里塞第二个 HTTP 响应，调用方只会看到一堆乱码。
        self._streaming = False
        try:
            self._check_auth()
            parsed = urlparse(self.path)
            # 存档名多半是中文，URL 里会变成 %XX，必须解码后再用
            parts = [unquote(p) for p in parsed.path.split("/") if p]
            query = parse_qs(parsed.query)
            self._route(method, parts, query)
        except ApiError as e:
            if not self._streaming:
                self._json({"error": e.message}, e.status)
        except (BrokenPipeError, ConnectionResetError):
            pass  # 调用方（浏览器/curl）提前断开，正常现象；回合已在后台跑完并落盘
        except Exception as e:
            traceback.print_exc()
            if not self._streaming:
                try:
                    self._json({"error": f"internal server error: {e}"}, 500)
                except Exception:
                    pass

    def _route(self, method, parts, query):
        # /v1/health
        if parts == ["v1", "health"] and method == "GET":
            return self._health()

        # /v1/saves
        if parts == ["v1", "saves"]:
            if method == "GET":
                return self._json({"saves": list_saves()})
            if method == "POST":
                return self._create_world(self._body())

        # /v1/worlds/chat   —— P9 世界创建向导（无状态，服务端不存会话）
        if parts == ["v1", "worlds", "chat"] and method == "POST":
            return self._wizard_chat(self._body())

        # /v1/worlds   —— 拿草稿生成完整世界并落盘
        if parts == ["v1", "worlds"] and method == "POST":
            return self._create_world(self._body())

        # /v1/saves/{name}/...
        if len(parts) >= 3 and parts[0] == "v1" and parts[1] == "saves":
            name = parts[2]
            rest = parts[3:]

            if not rest:
                if method == "GET":
                    if not _save_exists(name):
                        raise ApiError(404, f"save not found: {name}")
                    return self._json(get_engine(name).state_snapshot())
                if method == "DELETE":
                    if not _save_exists(name):
                        raise ApiError(404, f"save not found: {name}")
                    drop_engine(name)
                    delete_save(name)
                    return self._json({"deleted": name})

            if not _save_exists(name):
                raise ApiError(404, f"save not found: {name}")

            if rest == ["turn"] and method == "POST":
                return self._turn(name, query)
            if rest == ["assistant"] and method == "POST":
                return self._assistant(name)
            if rest == ["rollback"] and method == "POST":
                return self._rollback(name)
            if rest == ["save"] and method == "POST":
                engine = get_engine(name)
                engine.save()
                return self._json({"status": "ok", "round": engine.game.current_round})
            if rest == ["history"] and method == "GET":
                limit = int(query.get("limit", ["20"])[0])
                return self._json({"history": get_engine(name).history(limit)})
            if rest == ["events"] and method == "GET":
                return self._events(name, query)

        raise ApiError(404, f"no such endpoint: {method} {self.path}")

    # ---------- 端点实现 ----------

    def _health(self):
        from ..config import get_config
        cfg = get_config()
        provider = cfg.get("models", "main", "provider", default="deepseek")
        self._json({
            "status": "ok",
            "service": "AI Narrative RPG Service",
            "provider": provider,
            "api_key_configured": bool(cfg.get_api_key(provider)),
            "loaded_saves": sorted(_engines.keys()),
        })

    def _turn(self, name, query):
        body = self._body()
        user_input = (body.get("input") or "").strip()
        if not user_input:
            raise ApiError(400, "missing input (what the player does this turn)")
        push_through = bool(body.get("push_through", False))
        timeout = int(body.get("timeout") or query.get("timeout", ["300"])[0])
        engine = get_engine(name)

        if self._wants_stream(query, body):
            return self._turn_stream(engine, user_input, push_through, timeout)

        try:
            result = engine.run_turn(user_input, push_through=push_through, timeout=timeout)
        except TurnBusy:
            raise ApiError(409, "this save already has a turn in progress; wait for it to finish")
        if result["status"] == "ok":
            # 事件流已通过 narrative/events 给出，正文里再带一份完整的省得调用方翻
            result.pop("events", None)
        return self._json(result)

    def _turn_stream(self, engine, user_input, push_through, timeout):
        """SSE：叙事一边生成一边推给调用方，不用等整个回合跑完。"""
        holder = {}
        start = engine.current_seq()

        def _work():
            try:
                holder["result"] = engine.run_turn(user_input, push_through=push_through,
                                                   timeout=timeout)
            except TurnBusy:
                holder["result"] = {"status": "error", "error": "a turn is already in progress for this save"}
            except Exception as e:
                holder["result"] = {"status": "error", "error": f"turn failed: {e}"}

        runner = threading.Thread(target=_work, daemon=True)
        runner.start()

        self._sse_start()
        self._sse_send({"type": "start", "save": engine.save_name})
        since = start
        idle = 0.0
        try:
            while runner.is_alive() or engine.current_seq() > since:
                events, since = engine.wait_events(since, timeout=1.0)
                if events:
                    idle = 0.0
                    for event in events:
                        self._sse_send(event)
                else:
                    idle += 1.0
                    if idle >= 15:
                        idle = 0.0
                        self._sse_ping()
            # 收尾：把 runner 结束后可能还残留的事件也发出去
            for event in engine.events_since(since):
                self._sse_send(event)
            self._sse_send({"type": "result", **(holder.get("result")
                                                 or {"status": "error", "error": "unknown error"})})
            self._sse_end()
        except (BrokenPipeError, ConnectionResetError):
            pass  # 调用方断开，回合继续在后台跑完并落盘

    def _assistant(self, name):
        body = self._body()
        query = (body.get("query") or "").strip()
        if not query:
            raise ApiError(400, "missing query (what to ask)")
        timeout = int(body.get("timeout") or 120)
        try:
            result = get_engine(name).run_assistant(query, timeout=timeout)
        except TurnBusy:
            raise ApiError(409, "a query is already in progress for this save")
        result.pop("events", None)
        return self._json(result)

    def _rollback(self, name):
        ok, err = get_engine(name).rollback()
        if not ok:
            raise ApiError(409, err)
        return self._json({"status": "ok", "round": get_engine(name).game.current_round})

    def _events(self, name, query):
        """事件轮询。带 wait=N 就长轮询，最多阻塞 N 秒等新事件。"""
        engine = get_engine(name)
        since = int(query.get("since", ["0"])[0])
        wait = float(query.get("wait", ["0"])[0])
        if wait > 0:
            events, seq = engine.wait_events(since, timeout=wait)
        else:
            events, seq = engine.events_since(since), engine.current_seq()
        return self._json({"events": events, "seq": seq, "busy": engine.is_processing()})

    def _wizard_chat(self, body):
        """P9 世界创建向导一轮。无状态：历史与草稿由调用方持有并传回。"""
        player_input = (body.get("input") or "").strip()
        if not player_input:
            raise ApiError(400, "missing input (what the player says to the wizard)")
        result = wizard_turn(body.get("history") or [], player_input,
                             body.get("draft") or dict(EMPTY_DRAFT))
        if "error" in result:
            raise ApiError(502, result["error"])
        result.pop("raw", None)   # raw 是给界面版塞对话历史用的，调用方用不上
        return self._json(result)

    def _create_world(self, body):
        name = (body.get("name") or "").strip()
        err = _check_name(name)
        if err:
            raise ApiError(400, err)
        if _save_exists(name):
            raise ApiError(409, f"save already exists: {name} (DELETE it first to overwrite)")

        draft = body.get("draft")
        if not isinstance(draft, dict) or not any(draft.values()):
            raise ApiError(400, "draft (world draft) is empty. Use POST /v1/worlds/chat "
                                "to work out a draft with the wizard, or pass a filled-in draft directly.")

        progress = []
        save_name, err = create_save(name, draft, on_progress=progress.append)
        if err:
            raise ApiError(502, err)
        drop_engine(name)   # 万一同名的旧引擎还缓存着，清掉让它重新读盘
        return self._json({"status": "ok", "save": save_name}, status=201)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, api_token=None):
        self.api_token = api_token
        super().__init__(addr, handler)


def serve(host="127.0.0.1", port=8765, token=None):
    if token:
        try:
            token.encode("ascii")
        except UnicodeEncodeError:
            raise SystemExit(
                "Error: --token may only contain ASCII characters (letters, digits, symbols).\n"
                "HTTP request headers are encoded as latin-1, so a non-ASCII token arrives at "
                "the server as garbled bytes and every request fails with a 401. Rather than "
                "leave you staring at a 401 with no explanation, we say it up front. Pick a "
                "plain alphanumeric string instead.")
    httpd = Server((host, port), Handler, api_token=token)
    print(f"AI Narrative RPG Service started: http://{host}:{port}")
    print(f"  health check  GET  http://{host}:{port}/v1/health")
    print("  API documentation  docs/API.md")
    if host not in ("127.0.0.1", "localhost") and not token:
        print("  ⚠ Listening on a public address with no --token set: "
              "anyone can call your API key and rack up charges. Consider adding --token.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
