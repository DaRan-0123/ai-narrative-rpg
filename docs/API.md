# 服务版 API

把游戏的后台跑成一个 HTTP 服务，没有界面。

回合编排、状态管理、存档、风险判定、NPC 记忆、地图定位——桌面版里有的后台能力这里全都有，
只是把「画到窗口上」换成了「回一份 JSON」。别人可以拿它接自己的网页、Bot、客户端。

服务端和桌面版**共用同一份引擎代码**（`src/engine.py`），不是另写一套。
所以桌面版改进了什么，服务版跟着一起变。

## 跑起来

```bash
python serve.py                                    # http://127.0.0.1:8765
python serve.py --port 9000                        # 换端口
python serve.py --host 0.0.0.0 --token mysecret    # 让别的机器连，带令牌
```

- **零新依赖**：只用 Python 标准库（`http.server` + `json`）。`requirements.txt` 不变。
- **不需要 tkinter**：Linux / Docker 上也能跑（桌面版才需要 Windows + tkinter）。
- 默认只监听 `127.0.0.1`，只有本机能连。要对公网开就必须加 `--token`。

### 环境要求

- Python 3.10+
- 一个可用的 API 密钥（和桌面版共用同一份配置 `~/.ai_rpg_config.json`，
  也可以在环境变量里给，如 `DEEPSEEK_API_KEY`）

```bash
curl http://127.0.0.1:8765/v1/health
# {"status":"ok","api_key_configured":true,...}
```

`api_key_configured` 是 `false` 的话，先用桌面版配一次密钥。

---

## 最小示例

一个完整的来回：跟向导聊出世界 → 建世界 → 玩一回合。

```bash
B=http://127.0.0.1:8765

# 1) 跟世界向导聊（无状态，历史和草稿由你持有）
curl -s -X POST $B/v1/worlds/chat -H 'Content-Type: application/json' -d '{
  "input": "赛博朋克加修真，主角是用神经接口修炼的黑客",
  "history": [], "draft": null
}'
# → {"reply":"...","world_draft":{...},"is_done":false,"suggested_questions":[...]}
# 把返回的 world_draft 存下来，下一轮连同 history 一起传回去。

# 2) 草稿攒够了，生成完整世界并落盘（跑 P10→P6→P8，约 2-3 分钟）
curl -s -X POST $B/v1/worlds -H 'Content-Type: application/json' -d '{
  "name": "我的世界",
  "draft": {"magic":"灵气可量化为信号", "player_name":"陆沉", "narrative_style":"冷硬写实"}
}'
# → {"status":"ok","save":"我的世界"}

# 3) 玩一回合
curl -s -X POST $B/v1/saves/我的世界/turn -H 'Content-Type: application/json' \
  -d '{"input":"我拔掉接入线，起身查看这间机房。"}'
# → {"status":"ok","round":1,"narrative":"...","player_state":{...}}
```

存档目录和桌面版共用同一个 `saves/`。**服务版建的世界，桌面版打开就能接着玩**，反之亦然。

---

## 端点总表

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/health` | 健康检查、密钥是否已配 |
| GET | `/v1/saves` | 存档列表 |
| POST | `/v1/saves` | 建世界并落盘（同 `/v1/worlds`） |
| GET | `/v1/saves/{name}` | 当前完整状态快照 |
| DELETE | `/v1/saves/{name}` | 删除存档 |
| GET | `/v1/saves/{name}/history?limit=20` | 最近 N 轮 |
| POST | `/v1/saves/{name}/turn` | **跑一回合**（可 SSE 流式） |
| POST | `/v1/saves/{name}/assistant` | 游戏助手查询（问世界观/角色/关系） |
| POST | `/v1/saves/{name}/rollback` | 撤销上一回合 |
| POST | `/v1/saves/{name}/save` | 手动保存 |
| GET | `/v1/saves/{name}/events?since=N&wait=S` | 事件流（含后台任务） |
| POST | `/v1/worlds/chat` | P9 世界创建向导一轮（**无状态**） |
| POST | `/v1/worlds` | 拿草稿生成完整世界并落盘 |

存档名会出现在 URL 路径里，**中文名需要 URL 编码**（`curl` 会自动处理）：

```
GET /v1/saves/%E6%9C%8D%E5%8A%A1%E7%89%88%E6%B5%8B%E8%AF%95
```

---

## 跑一回合

```
POST /v1/saves/{name}/turn
```

```json
{
  "input": "我趁夜色翻过围栏，撬开后门进去看看。",
  "push_through": false,
  "stream": false,
  "timeout": 300
}
```

| 字段 | 默认 | 说明 |
|---|---|---|
| `input` | 必填 | 玩家这一回合要做什么 |
| `push_through` | `false` | 上一轮被风险门打回后，用来「放手一搏」 |
| `stream` | `false` | 换成 SSE 流式（也可用 `Accept: text/event-stream` 头） |
| `timeout` | `300` | 服务端等这回合最多几秒 |

返回的 `status` 有三种：

### `status: "ok"` — 正常完成

```json
{
  "status": "ok",
  "round": 16,
  "narrative": "你在大堂靠墙的长凳上坐下……",
  "player_state": {
    "current_location": "安塔利亚南区，港湾憩所旅店一楼大堂",
    "game_time": "清晨",
    "game_season": "秋季",
    "weather_environment": "雨停转晴",
    "physical_health": "右肩旧伤跳痛……",
    "current_scene_people": "旅店老板娘玛莎在柜台后擦锡杯"
  }
}
```

### `status: "needs_decision"` — 被风险门打回

**这是这个项目最核心的机制，接入时一定要处理。**

玩家的动作不是直接丢给叙事模型的。有一个独立的判定层先裁决这个动作是否成立、
代价是什么。有风险的动作**不会被执行**，而是打回来让你决定：

```json
{
  "status": "needs_decision",
  "action": "我趁夜色翻过围栏，撬开后门进去看看。",
  "reason": "翻围栏撬锁，伤体高危",
  "options": ["push_through", "retry"],
  "hint": "想继续就再发一次同样的输入并带 push_through=true；想换个做法就直接发新输入。"
}
```

调用方的两种走法：

```bash
# 放手一搏：接受代价，走 P14 双辩护人 → 裁判 P1 的裁定链路
curl -s -X POST $B/v1/saves/我的世界/turn -H 'Content-Type: application/json' \
  -d '{"input":"我趁夜色翻过围栏，撬开后门进去看看。","push_through":true}'

# 换个做法：直接发个新 input 就行，什么都不用带
curl -s -X POST $B/v1/saves/我的世界/turn -H 'Content-Type: application/json' \
  -d '{"input":"我先绕到仓库侧面，看看有没有没锁的窗。"}'
```

打回时**不消耗回合、不写存档**：`round` 不变，也不会产生可回退的快照。

### `status: "error"` — 出错

```json
{"status": "error", "error": "回合超时（300s），存档已自动保存到超时前状态"}
```

HTTP 状态码仍然是 **200**——请求本身被正常处理了，失败的是这一回合。
**接入方请判断 body 里的 `status`，不要只看 HTTP 码。**

---

## SSE 流式

叙事是边生成边推的，不用干等整个回合。

```bash
curl -N -X POST $B/v1/saves/我的世界/turn \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d '{"input":"我推门进去。"}'
```

```
data: {"type":"start","save":"我的世界"}

data: {"seq":24,"type":"player","text":"我推门进去。","push_through":false}

data: {"seq":25,"type":"system","text":"[正在生成叙事...]","is_player":false}

data: {"seq":26,"type":"narrative_delta","text":"你推开那扇"}

data: {"seq":27,"type":"narrative_delta","text":"锈住的铁门，"}

data: {"seq":31,"type":"turn_done","narrative":"你推开那扇锈住的铁门，……","round":16,"skip_narrative":true}

data: {"type":"result","status":"ok","round":16,"narrative":"……","player_state":{...}}
```

最后一条 `result` 事件和上面非流式的返回体**一模一样**——所以接入方可以只处理
`narrative_delta` 做打字机效果，然后拿 `result` 收尾，不用写两套逻辑。

15 秒没有新事件会发一个 SSE 注释行（`: ping`）保活，客户端会忽略它。

> 中途断开连接**不会中断回合**——回合继续在后台跑完并落盘。重连后用
> `/events?since=N` 或 `/history` 补齐即可。

---

## 事件流

`narrative_delta` 之外，引擎还有很多后台任务（P3 事实提取、P5 世界变化判定、
P11 剧情线回顾、P12 地图定位、NPC 记忆巩固）。它们的产出都进同一个事件流。

```
GET /v1/saves/{name}/events?since=0&wait=5
```

```json
{"events":[...], "seq":31, "busy":false}
```

- `since`：只要序号大于它的（默认 0 = 从头给）。把上次拿到的 `seq` 传回来就是增量。
- `wait`：长轮询，最多阻塞这么多秒等新事件。`0` 表示立即返回。
- `busy`：还有没有回合/后台任务在跑。
- 服务端只保留最近 2000 条事件；断太久就用 `/state` 和 `/history` 补齐，存档是完整的。

| 事件类型 | 载荷 | 含义 |
|---|---|---|
| `player` | `text`, `push_through` | 玩家输入回显 |
| `narrative_delta` | `text` | 叙事增量（流式） |
| `system` | `text`, `is_player` | 系统助手栏一行 |
| `turn_done` | `narrative`, `player_state`, `round` | 回合完成 |
| `risk` | `action`, `reason` | 风险门打回 |
| `error` | `message` | 出错 |
| `assistant` | `answer` | 助手查询答完 |
| `background_issue` | `message` | 后台任务失败（不影响游戏） |
| `p12_error` | `message` | 地图定位失败 |
| `rollback` | `round` | 已回退 |
| `saved` | `round` | 已保存 |

---

## 状态与历史

```
GET /v1/saves/{name}
```

一份完整快照：`round`、`date`、`season`、`time`、`weather`、`location`、`health`、
`items`、`people_in_scene`、`player_state`（原始字段全在里面）、`player_profile`、
`world_template`、`settings`、`threads`（活跃剧情线）、`known_facts_count`、
`npcs`、`map`、`can_rollback`、`busy`。

```
GET /v1/saves/{name}/history?limit=20
```

```json
{"history":[{"round":15,"input":"……","narrative":"……"}]}
```

叙事全文都在，自己渲染就行。

---

## 回退

```
POST /v1/saves/{name}/rollback
```

撤销上一回合：状态、叙事、存档一起回滚到回合开始前。成功返回新的 `round`。

失败返回 **409**（还有任务在跑 / 没有可回退的回合）。用状态里的 `can_rollback` 判断能不能点。

> 注意：回退后 `can_rollback` 仍是 `true`（快照没被清掉），但再点一次不会继续往前退——
> 快照只存一个回合。这是桌面版就有的行为，服务版保持一致。

---

## 世界创建

### `POST /v1/worlds/chat` — 向导对话

**完全无状态**：服务端不存任何会话。`history` 和 `draft` 由调用方持有，每次原样传回。

```json
{"history": [...], "input": "赛博朋克加修真", "draft": null}
```

```json
{
  "reply": "赛博朋克＋修真的混搭……",
  "world_draft": {"magic":"灵气可量化为信号","player_name":"陆沉"},
  "is_done": false,
  "suggested_questions": ["修炼的本质是什么？", "..."]
}
```

下一轮把 `world_draft` 当 `draft` 传回、往 `history` 里追加两条即可
（`{"role":"user","content":<你的输入>}` 和 `{"role":"assistant","content":<reply>}`）。
`is_done` 为 `true` 时草稿就够了。

### `POST /v1/worlds` — 生成世界

```json
{"name": "我的世界", "draft": {"magic":"...", "player_name":"..."}}
```

跑 P10 生成世界 → P6 完善初始 NPC → P8 细化世界细节，然后落盘。
**要 2-3 分钟**（好几次模型调用），建议把超时放长。

成功返回 **201**。存档已存在返回 409（先 DELETE 再建）。
`draft` 里至少要有一个非空字段。

---

## 错误码

| 码 | 什么情况 |
|---|---|
| 200 | 正常。**但 body 里 `status` 可能是 `needs_decision` 或 `error`** |
| 201 | 世界创建成功 |
| 400 | 请求体不是合法 JSON / 缺 `input` / 存档名非法 / `draft` 为空 |
| 401 | 配了 `--token` 但没带或带错 `Authorization: Bearer <token>` |
| 404 | 端点或存档不存在 |
| 409 | 同一存档已有回合在跑 / 存档已存在 / 没有可回退的回合 |
| 502 | 模型调用失败（上游 API 的问题） |
| 500 | 服务内部错误 |

出错一律是 `{"error": "人话描述"}`。

---

## 并发

**同一个存档同时只能跑一个回合**，第二个立刻拿到 409，不会排队也不会串味。
不同存档之间互不影响，可以并行。

同一个存档的助手查询和回合是两条独立的线，能同时跑。

---

## 接入示例

### Python

```python
import requests

B = "http://127.0.0.1:8765"

def play(save, action):
    while True:
        r = requests.post(f"{B}/v1/saves/{save}/turn",
                          json={"input": action}, timeout=300).json()
        if r["status"] == "needs_decision":
            print(f"⚠ {r['reason']}")
            if input("放手一搏？(y/n) ").lower() != "y":
                return None                      # 换个做法：直接发新动作
            action, push = action, True
            r = requests.post(f"{B}/v1/saves/{save}/turn",
                              json={"input": action, "push_through": push},
                              timeout=300).json()
        if r["status"] == "error":
            print("出错:", r["error"])
            return None
        print(r["narrative"])
        return r["player_state"]
```

### 流式（浏览器）

```js
const resp = await fetch(`${B}/v1/saves/${save}/turn`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ input: action }),
});
const reader = resp.body.getReader();
const dec = new TextDecoder();
let buf = "";
for (;;) {
  const { done, value } = await reader.read();
  if (done) break;
  buf += dec.decode(value, { stream: true });
  const lines = buf.split("\n\n");
  buf = lines.pop();
  for (const line of lines) {
    if (!line.startsWith("data: ")) continue;      // ": ping" 之类直接跳过
    const ev = JSON.parse(line.slice(6));
    if (ev.type === "narrative_delta") appendText(ev.text);
    if (ev.type === "result") finish(ev);
  }
}
```

---

## 注意

- **会花钱**：每回合好几次模型调用，费用由你自己的密钥承担。本地推理（Ollama / LM Studio）不花钱。
- **密钥不上网**：服务只连你配置的那个 AI 服务，密钥存在本机 `~/.ai_rpg_config.json`。
- **`--host 0.0.0.0` 且不带 `--token` = 谁都能用你的密钥烧钱**。启动时会警告，别忽略。
  `--token` 只能是 ASCII 字符（HTTP 头按 latin-1 编码，中文令牌永远比对不上，启动时会直接拦住）。
- **没有内置的用户体系**：这是给「自己接自己的程序」用的单用户服务。要多人用，自己在前面加一层。
- **存档在服务端本机**，没有云同步。
- 服务重启后引擎缓存清空，但存档都在磁盘上，`/state` 一读就回来了。
