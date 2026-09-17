# AI叙事RPG项目交接文档

> 本文档记录截至2026-07-28的所有已完成工作、当前架构和待办事项，供后续AI接手继续开发。

---

## 一、项目概述

这是一个基于大语言模型的**AI叙事RPG游戏**。核心特点：
- 纯文本驱动（当前阶段），美术方向 galgame 式立绘方案已于 2026-08-14 封存（见 4.23 节与 archive/galgame_portraits/ARCHIVE_NOTE.md；方案文档归档于 archive/galgame_portraits/docs/galgame_art.md，原 2D 像素方案归档于 archive/visualization/）
- 每轮玩家输入自由文本，AI生成客观叙事
- 世界动态演化，NPC有独立心理系统 + 情景记忆系统
- 游戏内日历驱动季节/天气连续演化
- P11故事师维护长期剧情线，叙事有方向感
- 所有AI调用分角色（P1-P11）处理不同任务

### 技术栈
- Python 3.10+
- ttkbootstrap（UI框架）
- DeepSeek API（主力/轻量均为v4-flash——2026-08-05 用户定：0731发布后flash已优于pro）
- 本地JSON文件存档系统

---

## 二、Prompt系统（P1-P12）

这是项目的核心架构。每个"P"是一个独立的AI角色，负责特定任务。

| P编号 | 名称 | 触发时机 | 功能 | 调用模型 | 关键参数 |
|-------|------|----------|------|----------|----------|
| **P1** | 叙事主Prompt | 每轮玩家输入 | 生成左框叙事文本（注入world_details、在场NPC档案+情景记忆、当前时节、剧情线） | 主力pro | temp=0.7, thinking=False |
| **P2** | 系统助手 | 玩家查询时 | 回答事实性问题 | 主力pro | temp=0.3 |
| **P3** | 事实提取 | 每轮叙事结束后 | 提取新事实 + 估算本轮经过时间(time_passed) + 判断天气变化(weather) + 记录季节迹象(season_sign) | 轻量flash | temp=0.3 |
| **P4** | NPC心理 | 有npc_notes时 | 分析NPC心理变化 + 产出情景记忆 memory_entry（可为null） | 主力pro | temp=0.7, thinking=False |
| **P4C** | NPC记忆巩固 | 某NPC的memory_log超过30条时（异步线程） | 把importance≤2且较早的旧记忆合并为一条"时期概述"（importance=2, tags=["概述"]），importance≥3原样保留 | 轻量flash | temp=0.3 |
| **P5** | 世界变化 | 每轮叙事结束后 | 判断是否有世界质变（⚠️ prompt已写好，代码中仍未集成） | 轻量flash | - |
| **P6** | 剧情师 | 新NPC出现时 | 设计完整NPC档案 | 主力pro | temp=0.7, thinking=False |
| **P7** | 世界初始化 | 创建世界时 | 生成初始世界设定（已废弃，由P10替代） | 主力pro | temp=0.8 |
| **P8** | 世界细化 | 创建世界后 | 补充货币/政府/法律等细节 | 主力pro | temp=0.7, thinking=False |
| **P9** | 世界创建向导 | 创建世界时（对话式） | 多轮对话引导玩家完善世界 | 主力pro | temp=0.8, thinking=False |
| **P10** | 世界创建完成器 | P9对话结束后 | 根据草稿生成完整P7格式数据 | 主力pro | temp=0.8 |
| **P11** | 故事师（剧情线回顾） | 每10轮一次（异步线程）+ 无线时首次孵化 | 维护长期剧情线：推进stage/next_beat、标resolved/dormant、孵化新线，active硬上限3条 | 主力pro | temp=0.7, thinking=False |
| **P12** | 地图师 | 每轮结束后程序门：current_location未注册才触发（异步线程） | 把主角实际身处的新地点定位为网格坐标/远方方位/none，并供给icon_subject图标题材 | 轻量flash | temp=0.3 |
| **P13** | 风险门 | 每轮玩家输入后、P1之前 | 只判断动作有没有"不成功的风险"（risk=true/false+15字理由）；失败/坏JSON一律按无风险放行 | 轻量flash | temp=0.1 |
| **P14** | 双辩护人 | 「放手一搏」确认后（顺序2次） | advocate列举优势/opponent列举劣势（"只许引用给定事实"硬约束），供裁判P1参考 | 轻量flash | temp=0.3 |

### Prompt顺序（创建世界时）
```
P9（多轮对话）→ P10（生成完整数据）→ P7（已废弃，由P10替代）→ P6（完善NPC）→ P8（细化世界）→ 存档
```

### Prompt顺序（游戏运行时）
```
P1（叙事）→ [状态修复] → [日历季节覆盖game_season] → [保存事实] → P4（NPC心理+记忆写入）
→ [P4C记忆巩固(>30条,异步)] → P3（事实+时间累加+天气写回）→ [P11剧情回顾(每10轮,异步)] → [保存存档]
```

设计文档：
- NPC记忆系统：`docs/npc_memory_design.md`
- 季节/天气动态：`docs/season_weather_design.md`
- 故事生成器：`docs/story_generator_design.md`

---

## 三、核心文件结构

```
E:\游戏项目1\
├── main.py                          # 程序入口
├── requirements.txt                 # 依赖
├── TODO.md                          # 待办清单（用户创建）
│
├── src/
│   ├── __init__.py
│   ├── config.py                    # 全局配置（API密钥、模型选择）
│   ├── api_client.py                # 所有AI调用封装
│   ├── game_state.py                # 游戏状态管理器（含记忆/日历/剧情线/地图数据逻辑 + 空间锚点纯函数）
│   ├── prompts.py                   # 所有Prompt模板（P1-P8、P4C、P11、P12）
│   ├── save_manager.py              # 存档读写（含story_threads/map_data持久化）
│   │   ⚠️ map_renderer.py / icon_gen.py 已于2026-08-14地图半封存时移入 archive/map_graphic/（见4.24）
│   │
│   └── ui/
│       ├── __init__.py              # 共用：窗口定位place_window + 全局视觉常量
│       ├── main_menu.py             # 主菜单界面
│       ├── new_game_dialog.py       # 创建世界对话框（P9+P10）
│       ├── game_window.py           # 游戏主界面（三带布局+各异步任务；地图按钮已随半封存移除）
│       │   ⚠️ map_window.py 已于2026-08-14地图半封存时移入 archive/map_graphic/（见4.24）
│       ├── debug_window.py          # 调试窗口（显示API请求）
│       └── settings_dialog.py       # 设置对话框
│
├── docs/
│   ├── data_flow.md                 # 数据流文档（详细，但部分已过时）
│   ├── background_images.md         # 背景图生成方案（AI模型选型）
│   ├── npc_memory_design.md         # NPC记忆系统设计（已实施）
│   ├── season_weather_design.md     # 季节/天气动态设计（已实施）
│   ├── story_generator_design.md    # 故事生成器设计（已实施）
│   └── galgame_art.md               # galgame立绘方案（现行美术方向，待接入游戏）
│
├── saves/                           # 存档目录
│   └── 存档1/
│       ├── world_template.json      # 世界观 + world_details（calendar字段为自定义历法预留接口）
│       ├── player_profile.json      # 主角信息
│       ├── player_state.json        # 当前状态（7必填+3可选+game_day游戏内天数）
│       ├── known_facts.json         # 已知事实数组
│       ├── action_history.json      # 行动历史数组
│       ├── story_threads.json       # 剧情线（P11维护，旧存档可无此文件）
│       ├── map_data.json            # 地图数据（P12维护：places注册表+far_places，旧存档可无）
│       ├── map.png                  # P12渲染的地图（程序生成，勿手改）
│       ├── settings.json            # 游戏设置
│       ├── meta.json                # 元信息（轮次、时间、last_story_review_round）
│       ├── npcs/                    # NPC档案目录（每个NPC含memory_log情景记忆）
│       │   ├── npc_001.json
│       │   └── ...
│       ├── locations/               # 地图图标目录（P12懒生成，locations/{地名}/icon.png）
│       └── archives/                # 归档历史（gzip）
│
├── galgame_test/                    # galgame立绘验证工具（见7.3节）
│   ├── bailian_gen.py               # 百炼生图脚本（t2i/edit双模式）
│   ├── prompt_templates.md          # prompt模板库+踩坑记录
│   └── output/                      # 验证图
│
├── archive/visualization/           # 像素可视化方案归档（已停用，可恢复）
│   └── ARCHIVE_NOTE.md
│
├── test_memory_live.py              # 真实API测试：NPC记忆读取链路
├── test_p4_write.py                 # 真实API测试：P4记忆写入+巩固链路
├── test_season_live.py              # 真实API测试：季节/天气链路
├── test_story_live.py               # 真实API测试：P11剧情线链路
│
└── assets/                          # 资源目录（预留）
    └── backgrounds/                 # 背景图（预留）
```

---

## 四、已完成的重大修改

### 4.1 创建世界流程重写（重要！）

**修改前**：选项式界面，11个下拉框选择世界维度
**修改后**：对话式AI向导（P9+P10），玩家自由描述，AI多轮引导

**文件**：`src/ui/new_game_dialog.py`（已完全重写）

**注意**：P9和P10的Prompt写在 `new_game_dialog.py` 里，不在 `prompts.py` 中。

### 4.2 P7简化（初始NPC只输出简述）

**文件**：`src/prompts.py` P7部分

### 4.3 P6"剧情师"改造

由AI自行判断是否需要秘密/关系/plot_hooks。

**文件**：`src/prompts.py` P6部分

### 4.4 P6使用主力模型

**文件**：`src/ui/game_window.py` NPC处理逻辑

### 4.5 P8"世界细化师"新增

输出10个字段（currency/government/law_system/economy_details/social_structure/culture/technology_magic/military_security/notable_locations/factions）。

**文件**：`src/prompts.py` P8部分

### 4.6 P8结果注入P1 Prompt

P1新增【世界具体细节】区块，从 `world_template.world_details` 读取。

### 4.7 P1 Prompt缓存优化（重要，后续改动须遵守）

Prompt区块顺序重排，把固定内容放在前面。**当前顺序（2026-08-14 版）**——原则：**固定块→低频块→增长块→每轮必变块**，稳定前缀最大化（DeepSeek 前缀缓存），每轮必变的内容全部收尾。此前 2026-07-28 版的【当前区域】/【剧情线】/【元设定】排在了变化区后面，永远落在 miss 区，2026-08-14 已全部前置：
```
【当前世界设定】      ← 固定
【世界具体细节】      ← 固定
【元设定】           ← 固定（2026-08-14 从末尾前置）
【当前区域】          ← 固定（2026-08-14 从已知信息后前置）
【当前时节】          ← 低频变化（天数/天气，见4.17）
【空间方位参考】      ← 低频变化（P12空间锚点，2026-08-14 新增）
【剧情线】           ← 低频变化（约10轮，无线时整块不输出，见4.18；2026-08-14 从历史后前置）
【玩家已知信息】      ← 增长块（追加式，前缀稳定；越往后增长越贵，截断策略见7.2.4）
【玩家当前状态】      ← 每轮必变
【在场NPC档案】      ← 每轮必变（含【记忆】小节，见4.16）
【最近对话历史】      ← 每轮必变
【当前轮次】         ← 每轮必变
【玩家输入】         ← 每轮必变
```
⚠️ 后续新增任何区块，先判断其变化频率再插入上述分层，勿打乱该顺序。

### 4.8 P1 new_entities规则强化

P1只返回"不在在场NPC档案中、且对剧情有实际推动作用"的新角色。

### 4.9 NPC去重检查

处理 `new_entities` 时按名字过滤已存在的NPC。

### 4.10 P6并发调用

多个新NPC同时出现时 `ThreadPoolExecutor(max_workers=3)` 并发调用P6。

### 4.11 叙事Prompt去文学化

P1_SYSTEM改为客观陈述风格，禁止比喻/拟人/象征/内心独白。

### 4.12 玩家已知信息改为全部

P1注入全部known_facts（⚠️ 无限增长的token风险仍在，见7.2）。

### 4.13 调试模式新增

游戏界面顶部"调试模式"开关，实时显示所有API请求和响应。

### 4.14 游戏界面布局调整

三列：左叙事 + 中查询 + 右状态（季节/时间/天气/位置/健康/物品/外貌/附近NPC）。

### 4.15 状态栏字体自适应

绑定 `<Configure>` 事件自动换行。

### 4.16 NPC记忆系统（2026-07-28，设计：docs/npc_memory_design.md）

NPC档案新增 `memory_log`（与 `psychology_log` 并列，职责分离：心理=当前状态滚动，记忆=情景事件累积）：

- **写入**：P4输出新增 `memory_entry` 字段（event/perception/importance 1-5/tags，无事可记为null）；importance标准：5=救命/背叛，4=大恩惠/大冲突，3=有意义的交谈，2=轻微摩擦，1=一面之缘
- **读取**：P1【在场NPC档案】每个NPC追加【记忆】小节，代码选取"最近2条+importance最高2条（去重≤4条）"；P4输入注入**全量**memory_log
- **巩固**：单个NPC记忆>30条时异步调P4C（flash），importance≤2且较早的合并为一条"时期概述"（强制importance=2, tags=["概述"]），importance≥3保留，最近5条琐碎记忆不参与合并
- 向后兼容：旧存档NPC无memory_log时所有读取返回空，P1不输出【记忆】块

**文件**：`src/prompts.py`（P4/P4C/P1）、`src/game_state.py`、`src/ui/game_window.py`
**遗留**：同分时取较新，旧的重要记忆会被挤出P1注入（仍保留在memory_log供P4参考）

### 4.17 季节/天气动态（2026-07-28，设计：docs/season_weather_design.md）

- **游戏内日历**：`player_state.game_day`（浮点累计天数）；默认历法一年4季×30天；`world_template.calendar` 为自定义历法预留接口
- **旧存档初始化**：无game_day时若有game_season则对齐到该季第一天（春1/夏31/秋61/冬91），否则为1
- **季节由日历确定**：每轮P1返回后用日历季节覆盖 `game_season`；P1注入【当前时节】块（第X天/季节/天气/连续性要求）
- **P3扩展**：输出增加 `time_passed`（代码解析累加：半天=0.5、次日=1、N小时÷24、解析失败兜底0.25天）、`weather`（默认延续上轮，叙事有明确依据才changed=true并写回）、`season_sign`（只记为普通fact，不改变日历季节，v1简化）
- 向后兼容：P3返回旧纯数组格式不报错

**文件**：`src/game_state.py`（parse_time_passed/get_season_for_day纯函数）、`src/prompts.py`（P3/P1）、`src/ui/game_window.py`
**遗留**：`game_time`（清晨/正午/深夜）仍由P1自由填写，未与日历小数联动

### 4.18 故事生成器P11（2026-07-28，设计：docs/story_generator_design.md）

- **剧情线档案**：`story_threads.json`（id/title/summary/stage/next_beat/involved/status/created_round/updated_round）；status=active/dormant/resolved，active硬上限3条（超出降级dormant）
- **触发**：简化规则——`current_round - meta.last_story_review_round >= 10`，或无线且轮次≥1时首次孵化（新旧存档统一走game_window异步线程，不接创建流程）；内存meta已同步防重复触发
- **输入**：现有剧情线 + 最近10轮历史摘要（每轮一行）+ 全场NPC的plot_hooks/秘密 + 最近30条facts + 世界观
- **注入P1**：【剧情线】块放【元设定】之前（低频变化，缓存友好），含active+dormant；P1_SYSTEM规则第8条"剧情线只作背景推力，玩家优先，不可强制拉拽"
- 真实验证（老陈的旅途15轮）：产出"码头魔药走私链""巴托的困境"两条线，伏笔兑现精准，next_beat具体可演

**文件**：`src/prompts.py`（P11）、`src/game_state.py`、`src/save_manager.py`、`src/ui/game_window.py`
**遗留**：剧情线对P1的隐性拉拽待实玩观察；resolved即时补回顾未做；NPC增多后P11输入需截断

### 4.19 P12网格地图系统（2026-08-04，设计定案：docs/p12_map_design.md；⚠️ 2026-08-14 地图半封存——图像部分已封存见 4.24，位置判断保留并接入 P1 空间锚点）

- **坐标系**：21×21 网格（x,y ∈ [-10,10] 整数），+x=北、+y=东，1格=100米，(0,0)=城市中心参考点
- **半封存标记（2026-08-14）**：图标懒生成、地图渲染、地图查看器、顶栏「地图」按钮、`map_test/`、`test_p12_live.py`、`assets/map/` 均已移入 `archive/map_graphic/`；**保留**：P12 定位链路（程序门→P12→校验→落库坐标）+ `map_data.json` 读写 + P1 空间锚点注入（见 4.24）
- **触发链路（双重门）**：每轮结束后程序门（`current_location` 名字未注册才触发）→ 异步线程调 P12（flash）→ P12门（确认主角身临其境，仅被提及输出 none）→ 校验清单 → 落库即锁定（一生只定位一次）
- **校验清单**（第七节，全部实测拦截通过）：JSON对象 / name回声==待安置地名（防"装傻"）/ far在八方位枚举 / 坐标整数 / 不越界 / 不撞格（断言，不仲裁）/ 坐标输出icon_subject非空。**失败直接报错提示用户，不自动重试**（用户定案2）
- **三选一输出**：坐标（含icon_subject）/ {far:八方位}（图外标签）/ {none:true}（正常结果，不落库不打扰）
- **存档扩展**：`map_data.json` = {places: {地名: {x, y, icon_subject, icon_file}}, far_places: [{direction, name}], version}；旧存档无此文件自动初始化空结构（向后兼容已验证）
- **图标懒生成**：落库后后台线程调 z-image-turbo（0.1元/张，裸HTTP），prompt=P12的icon_subject+程序固定构图块/风格块（白底/45度/粗墨线封闭轮廓）→ chroma_cut 容差28抠图 → `locations/{地名}/icon.png` 终身复用；破洞自检=保留像素占比<10% 自动重试1次（正常22-35%）；图标失败地点仍保留（地图上只有标签）
- **渲染**：`src/map_renderer.py` 把 proto_map.py 改造为正式渲染器（Figure对象API不碰pyplot全局状态，后台线程安全）；排版严格按设计文档第十节；输出存档目录 `map.png`；数据版本号+玩家坐标变化才重渲染；玩家标签自动抬到图标上方避让
- **地图窗口**：游戏窗口顶栏「地图」按钮 → `src/ui/map_window.py`（proto_map_viewer.py 改造的 Toplevel：DPI感知、滚轮以鼠标为中心缩放、左键拖拽、100%原大打开）；地图数据变化后窗口开着自动刷新；无地点时显示引导文案
- **复用资产**（只读引用 galgame_test，importlib 加载）：bailian_gen._call_raw_http/resolve_api_key、chroma_cut.cutout_white_bg；牛皮纸背景已复制到 `assets/map/parchment_dark.png`
- **真实链路验证**（`test_p12_live.py`，老陈的旅途）：P12 对「安塔利亚南区，港湾憩所旅店一楼大堂」输出 (0,-4) + 高质量icon_subject（11.2秒）；图标6.5秒、抠图保留28.5%；渲染0.7秒出 1952×2014 map.png

**文件**：`src/prompts.py`（P12_SYSTEM/build_p12_user）、`src/game_state.py`（map_data+MAP_LOCK）、`src/save_manager.py`（load/save_map_data）、`src/map_renderer.py`、`src/icon_gen.py`、`src/ui/map_window.py`、`src/ui/game_window.py`
**遗留**：地名注册表按字符串精确匹配（"码头仓库区"vs"码头"别名合并未定）；远方地点"走近之后"处理未定；~~P1空间锚点回喂~~ ✅ 2026-08-14 已做（地图半封存时落地，见 4.24）；玩家在已上图地点时红点压标签问题随图像封存失效

### 4.20 风险裁定链路（2026-08-05，用户定案；原型验证 test_adjudicate_live.py）

- **背景**："AI太好说话"问题——灰区动作（带伤老头赤手一挑三）被P1放行成无伤完胜（test_impossible_live.py）。用户否决骰子/数值方案，亲自设计本链路；原型实测：一挑三变惨胜、偷账本变失败
- **链路**：
  ```
  玩家输入 → [P13风险门 flash, temp=0.1] → 无风险 → 现有P1链路（完全不变）
                ↓ 有风险
          打回：叙事区「⚠ 你正在进行一个有风险的尝试」(error tag) + 确认条
               （「放手一搏」DANGER /「换个做法」SECONDARY，pack在输入框上方）
                ↓ 放手一搏
          P14双辩护人（顺序2次flash）→ build_p1_user（参数与正常链路完全一致）
          → build_p1_judge_user注入裁定块 → call_main_json(P1_SYSTEM, judge_user)
          → _handle_p1_result 统一处理（与正常链路完全一致）
                ↓ 换个做法
          什么都不执行，焦点回输入框，本轮作废（不调API、不写历史）
  ```
- **分叉实现**：原 `_process_left_input` 的 P1调用后处理**逐字提取**为 `_handle_p1_result(result, ok, user_input)`，正常/裁判两条链路共用；只有 p1_user 的来源分叉（裁判版末尾注入 `P1_JUDGE_BLOCK` 优势/劣势清单）
- **故障兜底**（用户定案：判定层故障不阻断游戏）：P13调用失败/坏JSON/缺risk字段 → 按无风险放行；P14失败一方 items 视为空清单（仍走裁判）；两方都失败 → 退回正常P1链路（不注入裁定块）
- **busy管理**：打回确认期间 `_set_left_busy(False)`（玩家可改输入）；放手一搏后重新 busy；「换个做法」不写历史
- **连续风险防护**：确认条显示期间提交新输入 → `on_left_submit` 先 `_hide_risk_confirm()` 隐藏旧确认条、按新输入重新走流程
- **确认条UI**：`risk_frame`（Label动作摘要截断30字 + 两按钮）默认不pack，`_show_risk_confirm` 时 `pack(before=left_input_frame)`，抉择后 `pack_forget`
- **P14为顺序调用**（非并行）：实现简单，总耗时实测~10秒内可接受；需要可改并行

**文件**：`src/ui/game_window.py`（on_left_submit/_process_left_input/_handle_p1_result/_run_risk_gate/_show_risk_confirm/_hide_risk_confirm/on_risk_go/on_risk_cancel/_process_risky_input/build_ui确认条）；`src/prompts.py`（P13_SYSTEM/build_p13_user/build_p14_prompts/P1_JUDGE_BLOCK/build_p1_judge_user，用户评审定稿，勿改）
**遗留**："[裁定中：双方分析员调查中...]"提示行会留在叙事区（有意保留作裁定痕迹）；真实手感待用户上机验证（GUI无法headless测试，冒烟已覆盖P13各分支/P14成败组合/退回路径/源码结构）

### 4.21 游戏窗口分辨率锁定（2026-08-05，用户需求："像游戏一样把分辨率设死"）

> 2026-08-05 修订：用户确认从「进入游戏时才锁」改为**「程序启动即锁，全程统一」**。

- **锁定落点（唯一）**：`main.py` `Application.__init__` 在 root 创建后立即调 `self._apply_resolution_lock()`（root 生命周期内恰好执行一次，不在 show_main_menu/_start_game 等会重复执行的路径）：读 `ui.resolution` 配置 → `parse_resolution()` 解析（失败兜底1920×1080）→ `geometry(f"{w}x{h}+{x}+{y}")` 屏幕居中 → `resizable(False, False)`；小屏只 print 警告不缩放
- **配置流**：主菜单设置区「游戏分辨率」下拉（readonly，旁注"重启程序后生效"）→ `config.set("ui", "resolution", ...)` → **下次启动程序时**生效
- **选项常量**：`src/ui/__init__.py` 的 `RESOLUTION_OPTIONS = ["1440×1080", "1920×1080"]`（乘号×）；`DEFAULT_RESOLUTION = "1440×1080"`（**4:3，2026-08-05 用户定案**，config.py 默认值同步）
- **主菜单适配**：菜单内容挂进居中容器 `self.inner`（`place(relx=0.5, rely=0.5, anchor="center", width=900, height=720)`），避免在固定 1920×1080 窗口里挤在左上；布局本身未动
- **已废弃**：原 `GameWindow._apply_resolution_lock/_release_resolution_lock`（进入游戏锁/返回主菜单解锁）全部移除；main.py 原 place_window 自适应+minsize 也被分辨率锁定取代（place_window 仍供各 Toplevel 使用：设置/新游戏/调试/地图窗口）
- **布局兼容**：锁的只是窗口外壳（游戏内布局后于 4.22 改为三带固定比例，PanedWindow 已撤）

#### 4.21.1 1920×1080锁定后的布局修复（2026-08-05 上机实测发现）

锁定后两处布局崩坏（均因布局写于窗口可调整时代，靠挤压分配空间）：

1. **右栏被挤没**（压成~20px竖条）：三栏是 `ttk.Panedwindow`（weight 6/2/3），weight 只在有富余空间时参与分配，锁定后右栏按内容最小宽度压死。修复：`build_ui` 末尾 `root.after(50, _init_sash_positions)` 显式钉死 sash——**左43% / 中35% / 右22%**（约820/670/420px，右栏保底可读）；窗口未完成布局时 after(100) 重试；设置后 sash 仍可拖拽
2. **顶栏按钮溢出**（"调试模式/地图/返回主菜单"被挤出屏）：pack 空间分配是"先 pack 先得"，原代码左侧信息标签先 pack、右侧按钮后 pack，空间不足时按钮被挤出。修复：**三个 RIGHT 控件的 pack 移到所有 LEFT 信息标签之前**——任何情况下按钮都在屏内，被压缩/截断的是位置文本（最长的那个）

**文件**：`src/ui/game_window.py`（build_ui 顶栏顺序重排 + `_init_sash_positions`）

**文件**：`main.py`（启动即锁）、`src/ui/__init__.py`（RESOLUTION_OPTIONS/parse_resolution）、`src/config.py`（默认值）、`src/ui/main_menu.py`（设置区下拉+菜单居中容器）、`src/ui/game_window.py`（旧逻辑移除）

#### 4.21.2 右栏状态栏卡片化（2026-08-06，用户需求："给每个分类及其文本加个深色带圆角的框"）

- **控件**：`game_window.py` 模块级新增 `StatusCard(tk.Canvas)`——tkinter/ttk 无原生圆角，用经典 **8 点 smooth 样条多边形画法**（`_draw_round_rect`，每角 3 控制点，`RADIUS=10`）
- **内容结构**：分类名（font 10，`CARD_TITLE_FG=#7f9bb3` 灰蓝次要色，顶部）+ 值文本（font 12，`COLOR_FG_MAIN`）；卡片底 `CARD_BG=#1c1f24`（比 darkly 背景 #222 更深）+ 微弱描边 `CARD_OUTLINE=#2e3641`；Canvas 自身底色用 `COLOR_BG_PANEL` 融入 Labelframe、`highlightthickness=0`
- **自适应**：`<Configure>` 只在**宽度变化**时重绘（高度变化是 `_redraw` 自己 `config(height=)` 触发的，不拦会死循环）；值文本用 Canvas 文本项 `width=` 参数随栏宽自动换行；`bbox()` 量两行文本总高后 `tk.Canvas.config(self, height=card_h)` 高度自适应
- **零调用点改动**（关键设计）：`_update_status_bar` 的 8 处 `status_labels[key].config(text=...)` 不动——`StatusCard.config` 拦截 `text=` 走 `set()`（空值占位"—"），其余参数透传基类。**坑**：`tkinter.Misc` 里 `configure` 是基类绑定的别名，必须 `configure = config` 两个都覆盖，否则 `configure(text=)` 会绕过拦截直达 Canvas 报 unknown option
- **build_ui 循环**：8 行 ttk.Frame+双 Label 结构 → 3 行 `StatusCard(...).pack(fill=X, padx=6, pady=4)`；`status_items` 的 8 个 key/中文名不变（season/time/weather/location/health/items/appearance/people）
- **验证**：`py_compile` 全模块通过；冒烟脚本（mock ttkbootstrap + 真 Tk withdrawn 窗口）25/25——类结构/别名/配色/占位/key 集合/8 处调用点/真机 set·config·configure/圆角多边形+双文本项存在/长文本高度>短文本（换行+自适应生效）

**文件**：`src/ui/game_window.py`（StatusCard 类 + 配色常量 + build_ui 状态栏循环替换）

#### 4.21.3 状态栏栏位与卡片行调整（2026-08-06，用户认可卡片设计后的两项修改）

**修改1：季节/时间/天气三卡片并成一行**（短值分类各占一张通栏卡太浪费）

- `status_frame` 内新增 `top_row = ttk.Frame`（`pack(fill=X, padx=6, pady=4)`），前三张卡片 `pack(side=LEFT, fill=BOTH, expand=YES)` 等分，卡片间 3px 缝（`padx=(0 if i==0 else 3, 0 if i==2 else 3)`）；其余 5 张通栏不变。`status_items`/`status_labels` 8 key 与 8 处 `config(text=)` 调用点零改动
- **关键坑（真机冒烟抓到）**：Canvas 默认需求宽度约 380px，三卡共需 ~1140px 超过行宽时 pack 赤字按**逆序饿死**（实测宽 [378,33,1]）。修复：`StatusCard.__init__` 加 `width=1` 自报极小需求宽度，等分才生效（修后实测 136/136/136）。此修复对通栏卡同样有益无害

**修改2：中栏/右栏互换**（原 左叙事/中助手/右状态 → 左叙事/**中状态**/**右助手**）

- `build_ui` 中两个 pane 代码块整体对调：状态栏块提前（容器改名 `status_col_frame`，weight=2），助手块置后（容器改名 `assistant_col_frame`，weight=3）；pane add 顺序 left_frame → status_col_frame → assistant_col_frame
- `_init_sash_positions` 比例同步：**左43% / 中22% / 右35%**（约 820/420/670px，中栏状态栏保底），旧 43/35/22 的中右互换
- `risk_frame` 确认无牵连：它挂在 `left_frame`（叙事栏输入框上方），与本次互换无关
- 语义注释清扫：模块 docstring 改"三栏布局：左=叙事区/中=状态栏/右=系统助手"；"右栏状态卡片配色"→"状态栏卡片配色"；`append_system`/`_notify_background_issue`/`_update_status_bar` 的"右框/右栏系统区/右侧状态栏"→"系统助手栏/中栏状态栏"；`on_right_submit` 等三处"右框查询"注释在互换后恰好名实相符（查询栏现在就在右列），保留；`right_input/right_query_btn` 变量名同理变为正确，不动

**验证**：`py_compile` 全模块通过；冒烟两轮——结构项 19/19（pane 顺序与 weight、容器归属、risk_frame 仍在左栏、sash 43/22、top_row 存在、8 key 与调用点不变），真机项 8/8（deiconify 真实布局：三卡等分 136/136/136、sash 变宽后重新等分且 Configure 重绘正常、窄卡长文本换行增高 85>64）

**文件**：`src/ui/game_window.py`（build_ui 两栏块对调+top_row 三卡行、`_init_sash_positions` 比例、StatusCard `width=1`、注释清扫）

#### 4.21.4 工作区高度适配（2026-08-06 上机实测：4:3窗口底部仍被屏幕下沿切掉一截）

**病根**：内容区1080 + 标题栏31 + 下边框8 + 任务栏48 > 屏幕可用高度，底带 pack(side=BOTTOM) 钉在客户区最底，被切的就是它。只改布局比例没用，必须让窗口整体（含外框）嵌进屏幕工作区。用户意图"把中间改小一点"由适配后的窗口自动达成（中间带吃剩余高度，窗口矮了它自然矮）。

**本机实测的两个关键事实**（写代码前先测定，勿凭记忆）：
1. **Tk `geometry +x+y` 定位的是外框左上角（含标题栏）**：请求+100+100 → 外框顶(100,100)，客户区原点(108,131)，左边框8px、标题栏+上边框31px。所以高度适配与居中都要按"外框=客户区+39"算
2. **Tk 正数字号=点阵**：main.py 加了 DPI 感知后，高缩放屏字体放大（本机200%会话实测行高 17→31px）；底带若钉死216px，卡片第二行被裁（实测溢出 300>216）

**修法**：
- `main.py`：模块级 `WINDOW_CHROME_H = 39`（标题栏31+下边框8，实测）；纯函数 `compute_locked_geometry(cfg_w, cfg_h, work_l/t/r/b)` → `(w,h,x,y)`：`h=min(配置高, 工作区高-39)`、`w=min(配置宽, 工作区宽)`、按**外框**在工作区内居中；`Application._get_work_area()` 用 `SystemParametersInfoW(SPI_GETWORKAREA=0x0048)` 查工作区（已扣任务栏），失败兜底 `全屏高-110`（本机 SPI 恰好失败，兜底路径实测通过）；`_apply_resolution_lock` 改为 工作区查询→纯函数→geometry→锁死，配置放不下时 print 适配日志
- `game_window.py`：底带高度 `BOTTOM_BAND_H=216` 改为 `measure_bottom_band_h()` 动态值——`max(216, round(216 × 实测行高 / 17))`（17=96DPI基线行高；**100%缩放用户机器上=216，行为不变**；高缩放屏随行高放大）；root不存在时 RuntimeError/TclError 兜底216
- `portrait_panel.py`：标题区高度弃用固定 TITLE_H=30，改 bbox 实测标题文本高（高DPI下13pt标题不被裁、图顶不压标题）

**真机冒烟 20/20**（本机 3840×2160@200% 会话=真实高DPI路径）：纯函数（1440×1080@工作区1920×1040 → `1440x1001+240+0`，外框底 1001+39=1040 精确贴合）；端到端（本机工作区3840×2050 放得下 → 不砍高度 `1440x1080+1200+465`，resizable锁死）；变矮三带（客户区1001下底带394完整在内、中间带557随窗口变矮、卡片300≤394不溢出）；立绘面板（变矮容器4:7保持、图顶不压标题、压到700高仍跟随不越界）

**遗留**：SPI_GETWORKAREA 在某些会话失败走兜底110px经验值；125%缩放机器上底带≈267px（占客户区~25%，中间带更矮，用户已接受"中间改小"方向）

**文件**：`main.py`（WINDOW_CHROME_H/compute_locked_geometry/_get_work_area/_apply_resolution_lock重写）、`src/ui/game_window.py`（measure_bottom_band_h）、`src/ui/portrait_panel.py`（标题bbox量高）

### 4.22 立绘版三带布局 + 立绘懒生成（2026-08-06，用户持示意图下达的大型UI重构）

**布局（弃用PanedWindow，固定比例place）**：窗口已锁1920×1080，示意图为固定分区，`build_ui` 重写为三带——
- 顶栏：通栏信息条（内容不变，标签统一 `font=FONT_TOP`）
- 中间带（`middle_band`，pack填充剩余高）：主角立绘 `pro_col.place(relx=0, relwidth=0.20)` / 主对话框 `narr_col.place(relx=0.20, relwidth=0.60)` / NPC立绘 `npc_col.place(relx=0.80, relwidth=0.20)`
- 底部带（`bottom_band`，**先 pack(side=BOTTOM) 预留空间**）：状态 `status_area.place(relx=0, relwidth=0.52)` / 游戏助手 `assistant_area.place(relx=0.52, relwidth=0.48)`
- 迁移零丢失：叙事区+输入框+risk_frame（还在输入框上方）→主对话列；"系统助手"→底部右侧改名"**游戏助手**"（示意图用词），`system_text` 设 `height=9, width=1` 让底带自然高度≈20%
- 状态卡片：撤掉4.21.3的三卡片行top_row，8张StatusCard改 **4列×2行** grid（`divmod(idx,4)`、`columnconfigure(weight=1, uniform="status_card")`、`sticky="ew"`），key与8处调用点继续零改动
- `_init_sash_positions` 与 PanedWindow 全删

**立绘懒生成链路**（设计定案 archive/galgame_portraits/docs/galgame_art.md；本节为 2026-08-06 历史实现，已随 4.23 封存）：
- `src/portrait_manager.py` `PortraitManager`：路径 `saves/{存档}/portraits/protagonist/{变体key}.png`、`portraits/npc/{名字安全化}/{变体key}.png`；`build_t2i_prompt` 按 prompt_templates.md 基础图模板（全身双约束+外貌服装显式+写实面部块+画风块）；`generate()` 复用 `bailian_gen._call`（qwen-image-2.0 SDK通路，prompt_extend=False，size=1536*2688），urllib下载 `.tmp` 后原子 replace；全程返回 (ok,msg) 不抛异常，空外貌拒绝生成（不花钱）
- **变体key暂用日历季节**（game_season，缺失"base"）——galgame_art §5.1 定案条件键全走P3，但P3的 appearance_changes 输出**尚未实施**（grep确认），季节是"现有产出"里唯一可用的；P3条件键落地后改 `variant_key()` 一处即可
- `src/ui/portrait_panel.py` `PortraitPanel(tk.Canvas)`：三态（占位文本/图片/坏图回退占位），标题13粗体，PIL等比缩放（min(宽比,高比)、LANCZOS、居中留深色边、`_tkimg`挂self防GC），`width=1,height=1`不自报尺寸，`bg=CARD_BG`与卡片同色
- `GameWindow` 钩子：`_refresh_portraits()` 挂在 `_update_status_bar()` 末尾（每轮必走）；以 `(变体key, 场景NPC名)` 检测状态变化，没变直接返回；缺图→占位"生成中…"+`_queue_portrait` 后台线程（`self._portrait_jobs` 同key去重）；完成 `_safe_after` force刷新；失败 `_notify_background_issue` 提示、下轮自动重试，不打断游戏
- **场景NPC口径**（裁量）：取 `get_npcs_in_scene()` 第一个（有存档档案、含appearance）；用户定案单张显示，结构留多张余地

**统一设计常量**（用户明确要求收编）：`src/ui/__init__.py` 新增 `FONT_SIZE_TOP=12 / FONT_SIZE_CARD_TITLE=10 / FONT_SIZE_BODY=12 / FONT_SIZE_PANEL_TITLE=13` + `CARD_BG/CARD_OUTLINE/CARD_TITLE_FG`（从game_window迁入）；game_window 顶部组字体元组 `FONT_TOP/FONT_BODY/FONT_PANEL_TITLE/FONT_CARD_TITLE/FONT_CARD_VALUE`；散装 `font=(FONT_FAMILY,14/12/11/10)` 全部收编（叙事正文14→**12**按用户梯度定案），grep校验零残留

**裁量点（如实记录）**：
1. 主角外貌=**建档appearance+player_state.appearance拼接**（用户指示"从player_state提取"，但存档实测state文本不含服装，单用会违反"服装必须显式"铁律导致自由发挥）
2. "系统助手"标题改"游戏助手"（示意图用词）
3. 叙事正文字号14→12（用户字号梯度定案"正文12"）
4. 底带高度不定死像素：内容自然高度≈20%（卡片随文本行数自适应，底带会随内容小幅伸缩）

**验证**：冒烟 49/49（布局静态A14+字体B5+链路静态C5+PortraitManager单元D15含mock生成落盘+PortraitPanel真机E9+F1）；真实生成1张主角立绘（老陈的旅途/秋季，**7.8秒、5654KB、1536×2688精确4:7**，目检：花白鬓角/左眉旧疤/发白长衫/褪色斗篷/药箱/铜药匙全部命中，厚涂油画风正确）

**遗留**：P3条件键（appearance_changes）实施后 `variant_key()` 迁移；NPC多张立绘扩展；美学三选项（平均颜值/身材风格块）尚未进prompt（存档无此字段，P9向导未加）；NPC懒生成将在玩家跑游戏时首次真实触发（每张0.2元，设计内）

**文件**：`src/ui/game_window.py`（build_ui重写+立绘钩子+字体收编）、`src/ui/portrait_panel.py`（新）、`src/portrait_manager.py`（新）、`src/ui/__init__.py`（统一常量）

#### 4.22.1 底部带不可见修复（2026-08-06 上机实测：主角/NPC立绘与主对话正常，底部带整段不见）

**病根（两个，主次分明）**：
1. **底部带高度塌缩为 1px（主病根，本地100%复现）**：底带用 `place(rely=0, relheight=1.0)` 布两个子区，设计指望"子区内容自然撑高底带"——但 **place 奴隶不会向 master 反推需求高度**（relheight 是相对 master 实际高度算的，master 自己的需求高度没有 pack/grid 奴隶可问，塌成 1px）。真Tk 1920×1080 实测：OLD 底带=**1px**、助手Text/输入框全部未映射、中间带吃掉1036px
2. **主程序无 DPI 感知（叠加嫌疑，无法本地验证但按地图坑先例必修）**：用户机器若开 Windows 显示缩放（如125%），DPI 不感知时 1920×1080 逻辑窗口被系统虚拟化成 2400×1350 物理，底带整段在屏幕下沿外；`map_test/proto_map_viewer.py` 2026-08-04 已踩过同坑，main.py 一直漏加

**修法**：
- `game_window.py`：新增常量 `BOTTOM_BAND_H = 216`（1080p 的 20%），`bottom_band = ttk.Frame(self.frame, height=BOTTOM_BAND_H)` + `pack_propagate(False)` 显式钉死，不再靠内容撑高；`system_text height=9` 注释改为"适配216px底带"
- `main.py`：启动最早处（窗口创建前）`ctypes.windll.shcore.SetProcessDpiAwareness(1)`（PROCESS_SYSTEM_DPI_AWARE，注释已写准语义）；感知后 `winfo_screenwidth` 返回物理像素，`_apply_resolution_lock` 居中计算（max(0,(sw-w)//2)）无需改

**修复后真机实测**（真Tk 1920×1080 复刻三带结构）：底带=**216px**、中间带=821px、状态卡 4列等宽 235/236px、卡片高 64/85/106 三档（最长"附近NPC"29字值也不溢出：最低底边203 ≤ 容器211）、助手Text=172px、输入框可见

**已知权衡/遗留**：底带钉死后内容超出 216px 会被裁（实测当前存档最长值不溢出；极端长文本场景接受裁切，卡片本身有换行自适应）；1080p 整窗 y=0 时标题栏约 31px 使客户区底边略出屏幕下沿（底带 216px 够高，视觉无感）；DPI 嫌疑无法在本机证实，若用户机器 100% 缩放则主病根就是塌缩

> 2026-08-06 后续：上述"标题栏使底边出屏"的遗留被上机证实（4:3 窗口底带第二行卡片+游戏助手被切），已由 **4.21.4 工作区高度适配**根治；216px 钉死改为随行高自适应（`measure_bottom_band_h`，100% 缩放下仍=216）

### 4.23 立绘功能封存（2026-08-14，用户决定：封存停用，不彻底删除）

- **决定**：galgame 立绘（主角/NPC 立绘懒生成 + 中间带左右立绘列）整体封存。游戏运行不再有任何立绘代码路径；所有代码/文档/产出完好归档，可随时恢复。
- **归档位置**：`archive/galgame_portraits/`（`ARCHIVE_NOTE.md` 全文说明归档内容/恢复方法）
  - `src/portrait_manager.py` → `archive/galgame_portraits/src/portrait_manager.py`
  - `src/ui/portrait_panel.py` → `archive/galgame_portraits/src/ui/portrait_panel.py`
  - `docs/galgame_art.md` → `archive/galgame_portraits/docs/galgame_art.md`（P12 图标定案第七节随行归档，`src/icon_gen.py` 注释引用已改指归档路径）
- **game_window.py 摘除点**：import 两行、`__init__` 立绘初始化（portrait_mgr/_portrait_jobs/_portrait_state）、build_ui 中间带立绘列（主对话列改占满 relwidth=1.0）、`_update_status_bar` 立绘刷新钩子、`_current_scene_npc`/`_refresh_portraits`/`_queue_portrait`/`_portrait_worker` 四个方法、模块 docstring
- **未动**：`galgame_test/` 整体保留（`bailian_gen.py`/`chroma_cut.py` 被 P12 地图图标管线 `src/icon_gen.py` 按需加载）；存档 `saves/{存档}/portraits/` 已生成立绘数据保留（代码不再引用）；`CARD_BG` 等视觉常量仍被状态卡片使用
- **4.22/4.22.1 节**：本节的立绘布局/懒生成设计已成历史（代码已摘除），如需恢复按 `archive/galgame_portraits/ARCHIVE_NOTE.md`「恢复方法」执行
- **验证**：`py_compile` 通过；`src/` 下 grep `立绘|portrait|Portrait|protagonist_panel|npc_panel|portrait_mgr|galgame_art` 零残留（仅 game_window 注释中封存说明）；`python main.py` 启动无 import 错误

### 4.24 地图功能半封存（2026-08-14，用户决定：封存图像，保留位置判断用于叙事）

- **决定**：P12 地图系统**半封存**——所有**图像相关**部分停用归档；**位置判断保留**（网格坐标定位链路 + map_data 读写），并**接入叙事**（P1 空间锚点回喂，HANDOVER 4.19 遗留项落地）。
- **归档位置**：`archive/map_graphic/`（`ARCHIVE_NOTE.md` 全文说明归档内容/恢复方法）
  - `src/map_renderer.py` → `archive/map_graphic/src/map_renderer.py`
  - `src/icon_gen.py` → `archive/map_graphic/src/icon_gen.py`
  - `src/ui/map_window.py` → `archive/map_graphic/src/ui/map_window.py`
  - `assets/map/`（parchment_dark.png）→ `archive/map_graphic/assets/map/`
  - `map_test/`、`test_p12_live.py` → `archive/map_graphic/`
- **game_window.py 摘除点（图像）**：顶栏「地图」按钮、`__init__` 的 `_map_window`/`_last_map_state` 初始化、主循环每轮 `_maybe_refresh_map_render()`、方法 `_get_map_png_path`/`_maybe_refresh_map_render`/`_current_map_state`/`_render_map_async`/`_do_render_map`/`on_open_map`/`_refresh_map_window`、`_run_p12` 内图标生成块与重渲染调用。
- **保留（位置判断，未动）**：`_maybe_run_p12`（程序门）/`_run_p12`（定位+落库）/`_validate_p12_result`/`_p12_short_label`/`_p12_report_error`/`_p12_running`；`game_state.py` 全部 map_data 方法；`save_manager.py` load/save_map_data；`prompts.py` P12_SYSTEM/build_p12_user。`map_data.json` 继续读写（新地点照常落库坐标，只是不生成图标不渲染）。
- **P1 空间锚点回喂（新增）**：`game_state.py` 模块级纯函数 `build_map_anchor_text(places, far_places, player_xy)` + `_direction_from_offset`（八方位分档，atan2(dx,dy)）；`prompts.py` `build_p1_user` 新增 `map_anchor_text` 参数，在【当前区域】块后注入【空间方位参考】块（相对玩家当前坐标的已上图地点方位：所在地→"你正位于此处"，其余→"方向约 N 米"，far→"远方·方向"）；`game_window.py` 新增 `_build_map_anchor_text()` 并在两处 `build_p1_user` 调用（正常/裁判链路）传入。玩家当前位置未上图时返回空串不注入。
- **未动**：`galgame_test/`（保留）；存档 `saves/{存档}/map.png`、`saves/{存档}/locations/`、`saves/{存档}/map_data.json`（数据保留，前两者代码已不再更新）；`requirements.txt`（matplotlib/numpy/pillow 现无运行消费但恢复时仍需，不改）。
- **验证**：`py_compile` 全部改动模块通过；`src/` 下 grep 图像残留零命中（map_renderer/icon_gen/map_window/on_open_map 等）；空间锚点纯函数单测通过（八方位 8/8 + 边界 + here/far/空坐标）；真实 Tk 实例化 GameWindow 成功、顶栏无「地图」按钮。

### 4.25 Token优化三连（2026-08-14，降低每轮API成本）

**1. P1 状态/天气零漂移规则（prompts.py）**
- P1_SYSTEM 新增规则9 + build_p1_user「重要提醒」新增10：player_state 各字段无客观变化时必须与输入【玩家当前状态】一字不改照抄；只有叙事有明确依据的字段才允许改写。
- P3_SYSTEM 新增规则7 + build_p3_user 注意区：weather 无变化（changed=false）时 current 必须与【上一轮天气】一字不改。
- 目的：治 HANDOVER 7.2.8 的"P1状态/事实漂移"，顺带让状态字段保持稳定（稳定=缓存命中更好）。

**2. P1 Prompt 块重排（prompts.py build_p1_user）**
- 原则：**固定块 → 低频块 → 增长块 → 每轮必变块**，稳定前缀最大化（DeepSeek 前缀缓存）。
- 旧版【元设定】【当前区域】【剧情线】排在变化区后面 → 永远 miss；2026-08-14 全部前置。
- 现顺序见 4.7 节（已更新为 2026-08-14 版）。⚠️ 后续加新块按变化频率分层插入。

**3. known_facts 截断（game_state.py select_facts_for_p1 + game_window.py 调用点）**
- `select_facts_for_p1(known_facts, limit=40)`：世界观基盘（source含"世界观"）全保留 + 最近一半 + 高置信 high 补足，去重 ≤ limit。
- P1 正常/裁判链路与 P14 注入传 `get_known_facts_summary(limit=40)`（game_window.py 662/967 行）；P2 查询仍用全量原始列表（带 source，1473/1485 行不变）；P11 剧情回顾用最近30条不变（1216 行）。
- 存档"老陈的旅途"41 条实测截到 39 条（世界观基盘 8 条全保留、无重复）。

**遗留**：known_facts 超长（几百条）后的"压缩归档"（合并语义相似事实、旧事实下线）未做；当前 40 条封顶已够长期使用。

### 4.26 P1 叙事流式输出（2026-08-14，主叙事窗口实时逐字显示）

- **需求**：P1 响应原本一次性等完整才显示（`call_main_json` 后 `_update_after_left` → `append_narrative`）；改为利用 P1 已知 JSON 输出结构，在**流式接收**中把 `narrative` 字段实时显示。
- **API 层（api_client.py）**：`APIClient.stream()` + 模块级 `call_main_stream(system, user, temperature, thinking)`。`stream:true` → SSE，`resp.iter_lines(decode_unicode=True)` 逐行解析 `data: {...}` 取 `choices[0].delta.content` 增量，yield `("content", delta)` / `("error", msg)`；非200/超时/SSE错误 yield error 后结束；`data: [DONE]` 正常结束。`call()`/`call_json()` 等既有层不动（P2/P3/P4/P6/P11/P12/P13/P14 仍非流式）。
- **UI 层（game_window.py）**：
  - `NarrativeExtractor`：增量从 JSON 文本提取 `"narrative"` 键值。正则定位键（转义 `\"narrative\"` 不误匹配）→ 逐字符收集值原始字符（处理 `\` 转义）→ 未闭合时 `json.loads('"' + raw + '"')` 前缀反解 + diff 输出增量（天然处理 `\n`/`\"`/`\uXXXX` 与多字节切分）；闭合引号不入 raw（外层包裹引号充当闭合，避免双引号非法）。
  - `_stream_p1(system, user, temperature)`：后台线程循环 `call_main_stream`，收集全文 + 喂 extractor，按 ~80ms 节流 `_safe_after(0, _append_streamed_narrative)` 实时插入叙事区；收完用与 `call_main_json` 相同的 markdown 清理 + json.loads 解析（失败 `call_main_json` 重试一次）。
  - `_append_streamed_narrative`：首块删除"[正在生成叙事...]"占位（`_remove_generating_placeholder`，用 search 可靠定位，替代旧 `lines[-2]` 行号猜测——该旧逻辑在文本以 `\n\n` 结尾时失效），随后逐字 insert。
  - 两条 P1 链路（`_process_left_input` 正常、`_process_risky_input` 裁判）都换 `self._stream_p1(P1_SYSTEM, ...)`；`_handle_p1_result(..., narrative_already_shown=True)` → `_update_after_left(..., skip_narrative=True)` 防重复 append。状态补全重试（`_handle_p1_result` 内 `retry_prompt`）仍非流式。
- **验证**：`py_compile` 全模块通过；NarrativeExtractor 单测 7 项（转义/前置/空值/单字符切分/多字节/unicode转义/尾斜杠）；mock 流式端到端（占位删除、转义解析、错误分支、skip_narrative 防重复、`_process_left_input` 全链路叙事显示+轮次推进）。
- **遗留**：真实 DeepSeek 流式尚未实机验证（本机无 key 冒烟 mock）；`delta.reasoning_content`（思考）未消费（thinking=False）；若网络抖动流式中断，已显示的叙事保留、该轮按错误处理。

### 4.27 回合回退功能（2026-08-14，撤销本回合回到上一回合）

- **需求**：游戏运行时保留一份"上一回合"隐形快照；本回合输出全部完成后可点顶栏「回退」按钮撤销本回合（叙事+状态+存档全回退，本回合当没发生过）。**单级撤销**。
- **核心设计**：快照在**每回合真正开始处理时（P1 调用前）**保存当前完整状态 = 上一回合结束态（上一回合已 save_all）。回合处理中/完成后快照始终指向"上一回合结束态"，点回退即恢复。
- **game_state.py**：`_rollback_snapshot` + `snapshot_for_rollback()`（deepcopy player_state/known_facts/action_history/meta/current_round/npcs/story_threads/map_data/pending*）/ `clear_rollback_snapshot()` / `has_rollback_snapshot()` / `restore_rollback_snapshot()`（恢复+save_all 落盘）。world_template/player_profile/settings 不回退。
- **game_window.py**：顶栏「回退」按钮（`warning-outline`，最右外沿，处理中禁用由 `_set_left_busy` 控制）；`on_left_submit` 提交时记录 `self._round_mark`（叙事区本回合起点）+ `clear_rollback_snapshot()`；正常/裁判两条 P1 链路在 `_stream_p1` 前 `snapshot_for_rollback()`；`on_rollback()`：处理中/无快照则提示 → 确认 → `restore_rollback_snapshot()` → 删除叙事区 `_round_mark→END` → `_update_after_left(..., skip_narrative=True)` 刷新HUD。风险门打回（未调P1）时快照被清空 → 点回退提示"无可回退的回合"。
- **验证**：临时存档 `saves/_tmp_rollback`（不碰真实存档，吸取 2026-08-14 存档污染教训）mock 一回合后回退——轮次 16→17→16、快照存在、叙事区删除本回合输入+叙事、存档 JSON 落盘回退、处理中/无快照守卫全过；headless 下主线程直接调 `_process_left_input`（无 mainloop 时线程内 `root.after` 会报 RuntimeError，真实游戏有 mainloop 不受影响）。
- **遗留**：单级撤销（新提交覆盖旧快照）；P3/P4C/P11/P12 异步线程在回退后若仍在跑会重写本回合部分数据（边缘情况未处理）。

### 4.28 全面健壮性修复（2026-08-14，3代理审查+逐项核实后"保守前提下能修都修"）

**🔴 严重（会崩/会坏）**
1. **返回主菜单双 destroy 崩溃**：`on_return_menu_click` 原来预销毁 frame，随后 `main._clear_frame` 再 destroy 同一 frame → TclError 白屏。修：`on_return_menu_click` 只调 `on_return_menu()`（game_window.py）；`main._clear_frame` destroy 前判 `winfo_exists()` + try/except TclError。
2. **config 浅拷贝污染 DEFAULT_CONFIG**（含 API key）：`DEFAULT_CONFIG.copy()` 浅拷贝，`set`/`_deep_update` 原地改嵌套 dict 污染模块级模板。修：`copy.deepcopy`；`save()` 改原子写（tmp+os.replace，与存档一致）。
3. **is_processing 漏后台线程**：不含 `_p12_running` 与记忆巩固 → 后台写盘时允许返回/关窗/回退。修：纳入 `_p12_running` + 新增 `_memory_consolidating` 计数（巩固线程 finally 复位）。

**🟠 线程安全/数据竞争**
4. 新增 `STATE_LOCK`（game_state.py）：保护 `player_state`（game_day 读写）、`known_facts`（add_fact/get_known_facts_summary 快照读）。
5. `get_npcs_in_scene`/`get_npc_memories_for_p1`/`get_npc_memory_count` 加 NPCS_LOCK 快照读。
6. `_consolidate_npc_memory` 读-改-写（log 快照 + replace）整体持 NPCS_LOCK，防同 NPC 并发巩固丢更新。
7. `snapshot_for_rollback`/`restore_rollback_snapshot` 加 NPCS_LOCK+MAP_LOCK+STATE_LOCK，防 P12/巩固并发撕裂快照。
8. `save_map_data` 持锁只取快照、锁外写盘（遵守"持锁不写盘"约定）。
9. debug_window 跨线程 `after` → 线程安全队列（`_log_queue`）+ 主线程 after 周期 drain（300ms），工作线程只入队。

**🟡 潜在 bug**
10. `_update_after_right` 占位删除改 search 可靠定位（同左路 `_remove_generating_placeholder`）。
11. `call` 返回 None（thinking 模式正文在 reasoning_content）→ 视为空响应重试；`_extract_content` 解析失败直接返回不重试（重发同一响应无意义）。
12. 重试加退避（0.5s 递增），防 429/5xx 连打限流。
13. `call_json` 重试分支剥 markdown fence（与首次解析一致）。
14. 分辨率默认值统一用 `DEFAULT_RESOLUTION` 常量（main_menu 原来硬编码 1920×1080）。
15. `archive_old_history` round 字段缺失/非整数时 `:04d` TypeError → 兜底。

**保守跳过（改坏风险 > 收益）**：`_call_p3_async` 改为真异步（P3 写 game_day/facts 与主循环 save_all 存在时序竞态，保持同步更安全）；stream 坏块跳过（标准 SSE 容错）；风险打回态（打回时快照已清，点回退提示"没有可回退"是合理行为）。

**验证**：全模块 py_compile 通过；临时存档回归（返回主菜单不预销毁、is_processing 含新标志、锁读写+快照、流式+回退仍工作）；500 轮并发读改写锁冒烟无异常。真实存档未触碰。

### 4.29 8-15/8-16 功能精进与 UI 统一（封装第一版前）

**UI 统一（8-15）**
- **三色统一**：全界面颜色收敛为 红(danger #d9534f)/蓝(primary #4582ec)/青蓝(info #17a2b8) 三色；绿/橙/黄等杂色全部并入；灰/黑/深色底/白字保留（不算颜色）。SUCCESS→INFO、WARNING→DANGER、助手绿字→青蓝、玩家输入→primary 蓝。
- **分辨率缩放**：`ui/__init__.py` 新增 `get_scale()`/`font_size()`/`SCALE`（scale = 分辨率高/1350）。字号、按钮 width、悬浮窗、窗口尺寸、**全部间距 padx/pady/padding**（约60处）随 SCALE 等比放大；1800×1350 时 SCALE=1.0 无变化。分辨率选项扩为 4 个 4:3：1800×1350/1920×1440/2048×1536/2560×1920。
- **改分辨率自动重启**：`_restart_app()`（main_menu.py），设置里改分辨率保存后确认即自动重启生效（曾修 `src/main.py` 路径 bug——正确路径 `dirname(__file__)+".."+".."`）。
- **字号加大**：叙事/状态栏正文 12→14、卡片分类名 10→12（所有分辨率）。
- 其他：设置/调试/世界向导/手动保存对话框尺寸调整、滚轮悬停 Combobox 不改选项、存档按数字自然排序、备份目录(带 _backup/_polluted/_tmp)过滤。

**功能精进（8-16）**
- **叙事日志窗口**：顶栏「日志」，按轮次只读回看全部历史（action_history）。
- **世界档案页**：顶栏「世界」，展示世界观/剧情线/NPC关系/世界大事记（source=世界事件）。
- **known_facts 压缩归档**：facts>100 时后台把最旧 30 条低置信事实合并成 2-3 条概述；**铁律：只移入 `archives/facts_archive.json` 完整归档、绝不删除**；世界观/世界事件/high 置信永不归档；50 轮内不重复。
- **设置测试连接**：SettingsDialog「测试连接」按钮，用当前 key 调一次轻量 API 验证。
- **风险失败代价强化**：P1_JUDGE_BLOCK 新增规则5——失败必须给具体可感知代价（受伤/损失物品/结仇/地位下降）并在 player_state 体现，不得毫发无损复起。
- P5 集成（8-15 已做）：P1 的 world_event 非空 → 后台触发 P5 深化 → 写 known_facts + 保守改写 world_description（未质变一字不改）。

**8-16 封装前稳定性检查**：全模块编译、全部界面 Tk 打开无 TclError、存档加载/回退/另存、回合推进、损坏 JSON/缺文件/配置损坏兜底、空输入拦截、真实启动 5 秒稳定——全部通过。

### 4.30 备份与翻新（2026-08-16，封装第一版）

- **备份**：`E:\游戏项目1 - 完善_backup_20260816\`（完整游戏，排除 Claude.msix/cc-win-x64.tgz/cc-extract/cc_meta.json/.claude/__pycache__）。
- **翻新**（对备份）：删除全部存档（saves 重建为空）、删除文件名中含历史密钥片段的残留文件、删除测试截图 _*.png、删除 vmp_enable.log；验证空存档 10 槽位、全部 .py 编译通过。
- **翻新后状态**：纯代码 + 空存档 + 无 key，首次运行需用户自行填 API key（在用户目录 ~/.ai_rpg_config.json，不在项目内）。
- **原项目** `E:\游戏项目1 - 完善\` 保持完整未动。

---

## 五、当前架构数据流

### 5.1 创建新游戏

```
用户打开创建界面
    ↓
NewGameDialog（对话式窗口）
    ↓
P9（多轮对话向导）→ 累积 world_draft
    ↓
用户说"就这样吧" → is_done=true
    ↓
P10（生成完整P7格式数据）
    ↓
P6（完善每个initial_npc）→ 并发调用
    ↓
P8（细化世界细节）
    ↓
GameState.init_new() → 保存所有存档文件
    ↓
打开 GameWindow
```

### 5.2 游戏主循环（每轮）

```
玩家输入
    ↓
P1（叙事生成）← 注入world_details + 在场NPC档案/记忆 + 当前时节 + 剧情线
    ↓
状态验证（修复缺失字段）
    ↓
更新游戏状态（player_state, action_history, facts_delta）
    │   └─ 日历季节覆盖 game_season
    ↓
处理new_entities（新NPC/地点/物品）
    │   ├─ 去重检查
    │   ├─ NPC → P6并发完善
    │   ├─ 地点 → 加入known_facts
    │   └─ 物品 → 加入known_facts
    ↓
P4（NPC心理分析+情景记忆写入）← 对每个有变化的NPC
    │   └─ memory_log>30条 → P4C巩固（异步线程）
    ↓
P3（事实提取+time_passed累加+weather写回+season_sign存fact）← 轻量模型
    ↓
P11剧情回顾检查（每10轮/首次孵化，异步线程）
    ↓
自动保存所有数据
    ↓
UI更新
```

---

## 六、关键数据结构

### 6.1 world_template（世界观）

```json
{
  "world_description": "世界观总体描述",
  "social_framework": "社会结构描述",
  "starting_area_description": "初始区域描述",
  "world_details": {          // ← P8生成（字段略，见4.5）
    "currency": {}, "government": {}, "law_system": {}, "economy_details": {},
    "social_structure": {}, "culture": {}, "technology_magic": {},
    "military_security": {}, "notable_locations": [], "factions": []
  }
  // "calendar": {"seasons": [{"name": "春季", "days": 30}, ...]}  // 自定义历法预留接口（未启用）
}
```

### 6.2 player_state（玩家状态，7必填+3可选+日历）

```json
{
  "current_location": "精确位置",
  "posture_action": "姿势和正在做的事",
  "clothing_equipment": "携带的装备和物品",
  "physical_health": "身体和精力状态",
  "transportation": "交通工具",
  "weather_environment": "天气和环境（P3判定changed=true才变化）",
  "current_scene_people": "当前场景中可感知的人",
  "game_season": "当前季节（由game_day日历时历确定，覆盖P1自由判断）",
  "game_time": "当前时间（可选，仍由P1填写，未与日历联动）",
  "appearance": "主角当前外貌（可选）",
  "game_day": 61.125
}
```

- `game_day`：游戏内累计天数（浮点，显示取整）。默认历法4季×30天：1-30春、31-60夏、61-90秋、91-120冬、循环
- 旧存档初始化：有game_season则对齐该季第一天，否则为1

### 6.3 NPC档案（P6生成 + 记忆系统扩展）

```json
{
  "npc_id": "npc_001",
  "name": "角色名字",
  "role": "身份/职业",
  "appearance": "外貌描述",
  "personality": "性格特点",
  "background": "背景故事",
  "relationship_to_player": "与主角关系",
  "relationships": { "角色名": "关系描述" },
  "psychology_log": ["初始心理状态"],
  "memory_log": [
    {
      "round": 9,
      "event": "玩家垫付了3银币替他赎回药箱",
      "perception": "觉得这人虽然穷但靠得住，欠他一次",
      "importance": 4,
      "tags": ["恩惠", "金钱"]
    }
  ],
  "secrets": ["秘密"],
  "plot_hooks": ["剧情线索"],
  "narrative_integration": "融入场景的描述"
}
```

- `memory_log`：情景记忆（2026-07-28新增，旧存档可无）。importance 1-5；>30条触发P4C巩固；P1注入"最近2+最重要2"

### 6.4 story_threads.json（剧情线，2026-07-28新增）

```json
{
  "threads": [
    {
      "id": "thread_001",
      "title": "码头魔药走私链",
      "summary": "这条线的来龙去脉",
      "stage": "萌芽",
      "next_beat": "下一个具体可演的剧情节点",
      "involved": ["老陈", "托比"],
      "status": "active",
      "created_round": 15,
      "updated_round": 15
    }
  ]
}
```

- status：active（≤3条）/ dormant（蛰伏可复活）/ resolved（不注入P1但保留）
- 旧存档无此文件时 `load_story_threads()` 返回 `{"threads": []}`

### 6.5 meta.json

```json
{
  "created_at": "...",
  "last_played": "...",
  "rounds": 15,
  "player_name": "老陈",
  "last_story_review_round": 15
}
```

- `last_story_review_round`：上次P11剧情回顾的轮次（2026-07-28新增）
- ⚠️ 注意：`game_day` 在 **player_state.json** 里，不在meta.json

---

## 七、待办事项（TODO）

### 7.1 用户明确要求的待办（✅ 2026-07-28 全部完成）

来自 `TODO.md` 的三项已全部实施并经真实API验证：

| 待办 | 状态 | 设计文档 | 测试脚本 |
|---|---|---|---|
| 1. NPC记忆 | ✅ 完成（P4写入/P1注入/P4C巩固） | docs/npc_memory_design.md | test_memory_live.py, test_p4_write.py |
| 2. 季节/天气判断 | ✅ 完成（游戏内日历+P3估算） | docs/season_weather_design.md | test_season_live.py |
| 3. 故事生成器 | ✅ 完成（P11剧情线，active≤3） | docs/story_generator_design.md | test_story_live.py |

测试存档 `saves/老陈的旅途/`（15轮真实历史）当前带有全套系统数据：玛莎6条记忆、巴托5条记忆、game_day=61.125（秋季）、2条active剧情线。备份在 `saves/老陈的旅途_backup_20260728/`。

### 7.2 已知待修复/优化

1. **实玩观察（最高优先级）**：记忆+日历+剧情线全套系统尚未在真实游戏中长跑验证。重点观察：剧情线是否对P1产生隐性拉拽（next_beat很具体，虽有"玩家优先"规则）
2. **P1思考模式关闭**：P1调用了 `thinking=False`，但DeepSeek v4-pro 可能仍返回thinking内容，需要确认是否生效
3. **P1缓存命中率**：虽然优化了Prompt顺序，但缓存命中率仍有限（~50-60%），需要进一步评估
4. **known_facts增长**：~~需要截断策略~~ ✅ 2026-08-14 已做：`game_state.select_facts_for_p1`（世界观基盘全保留 + 最近一半 + 高置信补足，limit=40 去重）；P1/P14 注入传 `get_known_facts_summary(limit=40)`，P2 查询仍用全量（带 source 引用，见 4.25）。真正超长（几百条）后的"压缩归档"策略仍未做，当前 40 条封顶已够长期使用
5. **P5未集成**：P5（世界状态变化判定）Prompt已写好但代码中未调用
6. **剧情线遗留**：resolved后即时补回顾未做（目前纯10轮间隔）；NPC增多后P11输入（plot_hooks汇总）需截断
7. **记忆系统遗留**：同importance时选取取较新，旧重要记忆会被挤出P1注入；`game_time`未与日历联动
8. **P1稳定性弱点（实玩发现）**：叙事中的事实与known_facts偶有漂移（自由发挥改写细节）；`get_npcs_in_scene`按NPC名字子串匹配current_scene_people，P1改写称呼（如只写"老板娘"）会导致该NPC档案/记忆当轮不注入

### 7.3 美术方向：galgame立绘（⚠️ 2026-08-14 已封存，见 4.23 节与 archive/galgame_portraits/ARCHIVE_NOTE.md）

> 2026-08-14 用户决定：galgame 立绘功能（接入主程序的部分）已封存停用，完整归档于 `archive/galgame_portraits/`。下文为定稿时记录，作为方案文档留档，不再代表当前主程序状态。

**方向决策**：像素小人（LPC骨架合成）已验证后**停用**——用户评审认为生成质量不稳定。所有管线、素材、文档完好归档于 `archive/visualization/`（恢复方法见其 `ARCHIVE_NOTE.md`），不是废弃。

**现行方案**：galgame 式立绘——每个角色 = **1 张全身设定图 + 懒生成换装/状态差分**。总文档 `archive/galgame_portraits/docs/galgame_art.md`，关键定案：

- **美学三选项**（存档级，玩家开局选择）：画风（厚涂油画/赛璐璐，新画风须实测入库）× 平均颜值（美型/写实）× 身材风格（漫画夸张/美型/写实）
- **懒生成 + 同步出图**：P6创建NPC那一刻才生成基础图；差分在状态第一次发生时才生成；生成即缓存到 `saves/{存档}/characters/{npc_id}/`，每张图终身复用。全分辨率出图实测约 **8.2 秒**，回合制节奏可接受，不需要异步架构（超时~60s兜底退回纯文本）
- **变体条件键全走 P3 判定**（入冬/受伤/换装等触发条件由P3从叙事判断）
- **生成服务**：阿里云百炼 `qwen-image-2.0`（文生图/图像编辑双模式，约 0.2 元/张）
- **工具**：`galgame_test/bailian_gen.py`（t2i/edit 双模式，--style/--size 参数）；prompt 规范 `galgame_test/prompt_templates.md`（三选项块+模板+踩坑记录）
- **关键踩坑**（必须遵守）：`prompt_extend` 必须关（智能改写导致指令漂移）；编辑模式必须显式传 `size=1536*2688`（否则输出压成半分辨率）；"丰满"要写"沙漏型/腰细/不肥胖"
- **API Key**：放 `galgame_test/api_key.txt`（一行，勿外传勿提交；脚本也支持脚本内填写或环境变量 `DASHSCOPE_API_KEY`）。⚠️ 该文件当前不在磁盘上，使用前需自行创建

**尚未接入游戏**：接入图纸见 `archive/galgame_portraits/docs/galgame_art.md`（P9开局三选项、P6外观字段、new_entities触发出图）。⚠️ 2026-08-14 已随立绘功能封存。

---

## 八、已知问题与注意事项

### 8.1 模型调用注意事项

- **主力模型**：`deepseek-v4-flash`（2026-08-05 起，P1/P2/P4/P6/P7/P8/P9/P10/P11/P12/P13/P14——0731发布后flash已优于pro，用户定案全部切换）
- **轻量模型**：`deepseek-v4-flash`（用于P3/P4C/P5）
- **thinking参数**：P1/P4/P6/P8/P11 已关闭，节省token
- **max_tokens**：默认4096，需要确认是否足够
- **异步线程任务**（均失败只记日志不阻塞回合）：P4C记忆巩固、P11剧情回顾

### 8.2 文件修改记录

以下文件已被修改（非原始模板）：
- `src/prompts.py` — 完全重写P1/P6/P7/P8，新增world_details注入；2026-07-28新增P4 memory_entry、P4C、P3时间/天气扩展、P1【当前时节】【剧情线】【记忆】块、P11
- `src/game_state.py` — 2026-07-28新增：NPC记忆方法、游戏内日历（parse_time_passed/get_season_for_day）、剧情线读写与触发判断
- `src/save_manager.py` — 保存world_details；2026-07-28新增story_threads持久化；2026-08-04新增map_data持久化
- `src/ui/new_game_dialog.py` — 完全重写为对话式（P9+P10）
- `src/ui/game_window.py` — 三列布局、并发P6、去重、debug模式；2026-07-28新增：P4记忆写入、P4C巩固、日历季节覆盖、P3扩展解析、P11异步回顾；2026-08-04新增：P12地图触发链路+地图按钮；2026-08-05新增：P13风险门+确认条+P14裁定链路（P1后处理提取为_handle_p1_result双链路共用）；2026-08-14：立绘摘除（4.23）+ 地图半封存（4.24，删图像钩子、保留P12定位、新增P1空间锚点注入）
- `src/api_client.py` — 未修改（标准接口）

### 8.2.1 P12地图系统注意事项（2026-08-04；⚠️ 2026-08-14 地图半封存，图像部分已归档 archive/map_graphic/，见 4.24）

- **当前活跃部分（保留）**：P12 定位链路（`_maybe_run_p12`/`_run_p12`/`_validate_p12_result`/`_p12_short_label`/`_p12_report_error`，`_p12_running` 防重入）；`map_data.json` 读写；P1 空间锚点注入（`game_state.build_map_anchor_text`，`build_p1_user` 的 `map_anchor_text` 参数）。新地点照常落库坐标，不再生成图标/渲染地图。
- **已封存（移入 archive/map_graphic/）**：图标懒生成（`icon_gen.py`，z-image-turbo+chroma_cut）、地图渲染（`map_renderer.py`，matplotlib Figure API）、地图查看器（`map_window.py`）、`assets/map/parchment_dark.png`、`map_test/`、`test_p12_live.py`。恢复方法见归档 `ARCHIVE_NOTE.md`。
- **历史依赖说明**：图标生成曾复用 `galgame_test/bailian_gen.py` key 解析（脚本内API_KEY / 环境变量 DASHSCOPE_API_KEY / api_key.txt）；渲染/查看器曾用 pillow/matplotlib/numpy（requirements.txt 保留未删，恢复时仍需）。
- **线程模型（保留部分）**：P12 定位在后台线程（`_p12_running` 防重入）；UI 刷新一律走 `_safe_after`。

### 8.3 模拟存档

用户要求创建的模拟存档（拟真数据），位于 `saves/老陈的旅途/`。2026-07-28修复了5个JSON文件的未转义引号语法错误（备份在 `saves/老陈的旅途_backup_20260728/`）。

模拟存档包含：
- 世界观设定（蒸汽工业+中魔，烬土大陆安塔利亚城邦）
- 主角"老陈"（流浪医师）
- 5个NPC（经过P6完善）：艾琳娜、巴托、托比、莉莉、玛莎
- 15轮action_history、41条known_facts
- 玛莎6条情景记忆、巴托5条情景记忆（含真实P4产出1条）
- game_day=61.125（第61天·秋季）
- 2条active剧情线（码头魔药走私链、巴托的困境）

### 8.4 调试模式

游戏界面顶部有"调试模式"开关。开启后：
- 弹出调试窗口
- 实时显示所有API请求的URL、model、temperature、Prompt预览、响应状态码
- 窗口可关闭/重新打开

### 8.5 真实链路测试脚本（项目根目录）

| 脚本 | 验证内容 | 复跑成本 |
|---|---|---|
| `test_memory_live.py` | NPC记忆读取链路：P1注入【记忆】+真实P1自发回忆 | 1-2次P1调用 |
| `test_p4_write.py` | P4记忆写入：memory_entry质量/写入/持久化/巩固（mock flash） | 2次P4调用 |
| `test_season_live.py` | 季节天气：P3时间累加/天气延续与变化/【当前时节】块 | 2次P3调用 |
| `test_story_live.py` | P11剧情线：首次孵化质量/落盘/触发关闭/【剧情线】块（已有线时0调用） | 0-1次P11调用 |

⚠️ 这些脚本会读写真实存档 `saves/老陈的旅途/`（改动均为合理剧情资产）；跑之前确认备份存在。

---

## 九、接口参考

### 9.1 API调用函数（src/api_client.py）

```python
call_main(system, user, temperature=0.7) -> (text, success)
    # 调用主力模型，返回纯文本

call_main_json(system, user, temperature=0.7, thinking=False) -> (dict, success)
    # 调用主力模型，返回JSON（自动解析）

call_lightweight(system, user, temperature=0.3) -> (text, success)
    # 调用轻量模型

set_debug_mode(enabled)
is_debug_mode() -> bool
```

### 9.2 GameState关键方法（src/game_state.py）

```python
load()                              # 从存档加载所有数据（含story_threads）
init_new(world_data, player_info, settings)  # 初始化新存档
get_npcs_in_scene() -> dict         # 获取当前场景中的NPC（按名字子串匹配，有弱点见7.2.8）
get_known_facts_summary() -> list   # 获取所有事实的内容文本
get_recent_history(limit=10) -> list
add_fact(fact_data)                 # 添加事实
update_player_state(new_state)      # 更新玩家状态
add_npc_from_entity(entity) -> str  # 从new_entities创建NPC（返回npc_id）
add_npc_psychology(npc_id, entry)   # 追加NPC心理日志
record_round(player_input, narrative, ai_output)
save_all()                          # 保存所有数据

# NPC情景记忆（2026-07-28）
add_npc_memory(npc_id, entry)       # 追加memory_log（无字段自动初始化）
get_npc_memories_for_p1(npc_id, max_items=4)  # 最近2+最重要2，去重≤4
get_npc_memory_count(npc_id) -> int
replace_npc_memories(npc_id, new_log)  # 巩固后整体替换
select_memories_for_p1(memory_log, max_items=4)  # 模块级纯函数

# 游戏内日历（2026-07-28）
get_game_day() -> float             # 旧存档对齐初始化
add_game_days(days)                 # 累加（≤0按0.25兜底）
get_season() -> str                 # 日历驱动季节
get_date_display() -> str           # "第X天 · 季节"
parse_time_passed(text) -> float    # 模块级纯函数，解析失败0.25天
get_season_for_day(game_day, calendar=None)  # 模块级纯函数，支持自定义历法

# 剧情线（2026-07-28）
get_story_threads() -> list         # 全部（含resolved）
get_active_threads() -> list        # active+dormant（供P1注入）
update_threads(new_list)            # 整体替换+清洗（active>3降级dormant，元信息沿用）
should_run_story_review() -> bool   # 距上次≥10轮 或 首次孵化
```

### 9.3 SaveManager关键方法（src/save_manager.py）

```python
init_new_save(world_data, player_info, settings) -> bool
load_world_template() -> dict
load_player_profile() -> dict
load_player_state() -> dict
load_known_facts() -> list
load_action_history() -> list
load_settings() -> dict
load_all_npcs() -> dict
load_story_threads() -> dict        # 无文件返回 {"threads": []}
load_meta() -> dict
save_player_state(state)
save_known_facts(facts)
save_action_history(history)
save_npc(npc_id, data)
save_settings(settings)
save_story_threads(data)
save_meta(meta)
update_meta(**kwargs)
check_archive_trigger(history, trigger=250) -> bool
archive_old_history(history, chunk_size=100) -> (remaining, filename)
```

---

## 十、后续开发建议

### 最高优先级：实玩验证
1. **真实游戏跑几轮**：用 `saves/老陈的旅途` 观察全套系统（记忆+日历+剧情线）联动表现——NPC是否引用记忆、天气是否连续、剧情线是否自然带向next_beat且不强推

### 高优先级
2. ~~**接入galgame立绘**~~ ✅ 2026-08-14 用户决定**封存停用**（非废弃），本待办作废。归档与恢复方法见 4.23 节与 archive/galgame_portraits/ARCHIVE_NOTE.md
3. **known_facts截断**：当事实太多时，P1的token会爆炸，需要截断策略

### 中优先级
4. **P5集成**：把P5（世界状态变化判定）接入游戏循环
5. **剧情线补强**：resolved即时补回顾；P11输入按NPC活跃度截断；实玩后评估是否要给P1加抗拉拽措施
6. **game_time联动日历**：time_passed小数部分驱动清晨/正午/深夜
7. **场景NPC匹配加固**：get_npcs_in_scene 改为名字+别名/角色多重匹配

### 低优先级
8. **季节迹象修正历法**：season_sign积累后做历法微调（数据已在积累）
9. **音效/音乐**：氛围音效
10. **存档管理**：导出/导入、云同步
11. **像素可视化**：已归档（archive/visualization/），如需恢复见 ARCHIVE_NOTE.md

---

*本文档由AI助手更新于2026-07-28，供后续开发参考。*
