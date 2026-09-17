# 游戏数据流文档

> 本文档详细描述AI叙事RPG游戏中所有核心数据流动过程。

---

## 一、游戏启动数据流

### 1.1 程序入口（main.py）

```
用户双击运行程序
    ↓
Python 执行 main.py
    ↓
创建 Application 实例
    ↓
初始化 ttkbootstrap 主窗口（1600×1000，darkly主题）
    ↓
加载全局配置（config.py → ~/.ai_rpg_config.json）
    ↓
    ├─ API密钥（DeepSeek/OpenAI/Anthropic/Gemini）
    ├─ 模型选择（主力模型 + 轻量模型）
    └─ 游戏参数（历史轮数限制、归档触发阈值）
    ↓
显示主菜单（MainMenu）
```

### 1.2 主菜单显示（main_menu.py）

```
MainMenu.build_ui()
    ↓
查询所有存档状态（save_manager.list_saves()）
    ↓
遍历 saves/ 目录下的10个存档槽位
    ↓
对每个存档槽位：
    ├─ 读取 meta.json（轮次、最后游玩时间）
    ├─ 存在 → 显示"继续"按钮 + "删除"按钮
    └─ 不存在 → 显示"新建"按钮
    ↓
渲染存档列表到主菜单界面
```

---

## 二、创建新游戏数据流

### 2.1 打开创建对话框（new_game_dialog.py）

```
用户点击"新游戏"或"新建"按钮
    ↓
Application.on_new_game(save_name)
    ↓
创建 NewGameDialog 对话框（700×600）
    ↓
显示两页标签：
    ├─ 第一页：世界设定（7个维度下拉框 + 4个元设定）
    │   ├─ 魔法体系：无魔法/低魔/中魔/高魔
    │   ├─ 科技水平：原始/蒸汽工业/电气/信息/赛博/星际
    │   ├─ 社会形态：部落/封建/城邦/帝国/资本主义/社会主义/企业统治/无政府/神权
    │   ├─ 经济状况：繁荣/稳定/衰退/崩溃/重建
    │   ├─ 社会秩序：安定/动荡/战争/革命/混乱
    │   ├─ 道德氛围：黑白分明/灰色地带/道德虚无/理想主义
    │   └─ 初始区域：大都会/边境小镇/孤岛/地下城/游牧营地/荒野/港口/废弃区
    │
    └─ 第二页：主角创建
        ├─ 模式A：自定义（名字 + 外貌 + 背景）
        └─ 模式B：预设（3个固定预设可选）
```

### 2.2 生成世界（调用P7）

```
用户点击"开始游戏"
    ↓
收集所有世界维度选项 → world_options 字典
收集主角信息 → player_info 字典
    ↓
构建P7 Prompt（build_p7_user）
    ↓
调用主力AI模型（call_main_json, thinking=False）
    │
    ├─ 发送内容：
    │   ├─ system: P7_SYSTEM（世界观构建专家）
    │   └─ user: 世界维度 + 主角信息 + 输出格式要求
    │
    └─ AI返回JSON：
        ├─ world_description（世界观总体描述，300字）
        ├─ social_framework（社会结构，200字）
        ├─ starting_area_description（初始区域，300字）
        ├─ initial_npcs[]（NPC数组，至少3个）
        │   ├─ npc_id, name, role, appearance
        │   ├─ personality, background
        │   ├─ relationship_to_player
        │   ├─ psychology_log[]
        │   └─ secrets[]
        ├─ initial_situation（开场叙事，200字，第二人称）
        └─ initial_player_state（初始玩家状态，7个必填字段）
            ├─ current_location（当前位置）
            ├─ posture_action（姿势动作）
            ├─ clothing_equipment（穿着装备）
            ├─ physical_health（身体健康）
            ├─ transportation（交通工具）
            ├─ weather_environment（天气环境）
            └─ current_scene_people（在场人物）
    ↓
解析AI返回的JSON
    ↓
构建 settings 字典（叙事风格、助手语气、视角、难度、历史限制）
    ↓
调用 GameState.init_new(world_data, player_info, settings)
    ↓
SaveManager.init_new_save() 执行以下写入：
    ├─ 创建目录结构（saves/存档名/npcs/locations/archives）
    ├─ world_template.json ← 世界观描述
    ├─ player_profile.json ← 主角信息
    ├─ player_state.json ← 初始状态（7字段）
    ├─ known_facts.json ← 初始事实（世界观+主角+NPC+位置）
    ├─ action_history.json ← 空数组
    ├─ settings.json ← 游戏设置
    ├─ npcs/npc_001.json ← 每个NPC独立文件
    └─ meta.json ← 创建时间、轮次=0、主角名
    ↓
关闭对话框
    ↓
Application._start_game(save_name)
    ↓
创建 GameWindow 实例
```

---

## 三、游戏主界面加载数据流

### 3.1 初始化游戏窗口（game_window.py）

```
GameWindow.__init__(root, save_name, on_return_menu)
    ↓
创建 GameState(save_name)
    ↓
GameState.load() 从存档读取所有数据：
    ├─ world_template ← world_template.json
    ├─ player_profile ← player_profile.json
    ├─ player_state ← player_state.json
    ├─ known_facts ← known_facts.json
    ├─ action_history ← action_history.json
    ├─ settings ← settings.json
    ├─ npcs ← npcs/ 目录下所有.json文件
    ├─ meta ← meta.json
    └─ current_round ← meta["rounds"]
    ↓
构建三列UI布局：
    ├─ 左列（60%）：叙事文本区 + 发送输入框
    ├─ 中列（20%）：系统助手标题 + 助手对话区 + 查询输入框
    └─ 右列（20%）：状态栏（8项实时信息）
    ↓
显示初始叙事（show_initial_narrative）
    ↓
    ├─ 显示【你是谁】：名字、外貌、背景
    ├─ 显示【世界概况】：世界观描述、社会形态
    ├─ 显示【你在哪】：区域描述、当前位置、在场人物
    ├─ 显示【故事开始】：initial_situation 开场叙事
    └─ 如果 current_round > 0：追加显示最近3轮历史
    ↓
初始化状态栏（_update_status_bar）
    └─ 从 player_state 读取：季节、时间、天气、位置、健康、物品、外貌、附近NPC
```

---

## 四、核心游戏循环数据流（玩家输入→AI响应→保存）

### 4.1 玩家输入阶段

```
玩家在左列输入框输入文字，按回车或点击"发送"
    ↓
GameWindow.on_left_submit()
    ├─ 获取输入文本
    ├─ 清空输入框
    ├─ 在叙事区追加显示玩家输入（带"input"标签）
    ├─ 禁用输入框（防止重复提交）
    └─ 显示"[正在生成叙事...]"提示
    ↓
启动后台线程（threading.Thread）
    └─ 执行 _process_left_input(user_input)
```

### 4.2 AI叙事生成阶段（P1主循环）

```
_process_left_input(user_input) 在后台线程执行：
    ↓
获取当前场景中的NPC（GameState.get_npcs_in_scene()）
    └─ 根据 player_state["current_scene_people"] 匹配NPC名字
    ↓
获取最近N轮历史（GameState.get_recent_history()）
    └─ 默认10轮，从 action_history 末尾截取
    ↓
获取已知事实摘要（GameState.get_known_facts_summary()）
    └─ 取**全部**已知事实的内容文本（不是最近20条，是全部）
    └─ 文件位置：src/game_state.py 第57-65行
    └─ 作用：让AI知道玩家从开局到现在积累的所有信息
    ↓
构建P1 Prompt（build_p1_user）：
    ├─ 当前世界设定（world_description + social_framework）
    ├─ 当前区域描述（starting_area_description）
    ├─ 玩家当前状态（7个核心字段 + 3个可选字段）
    ├─ 在场NPC档案（身份、性格、最近3条心理日志、关系）
    ├─ 玩家已知信息（**全部**已知事实，不是摘要）
    ├─ 最近对话历史（最近10轮，每轮含输入+叙事摘要）
    ├─ 元设定（叙事风格、视角）
    ├─ 当前轮次
    └─ 玩家输入
    ↓
调用主力AI模型（call_main_json, thinking=False）
    │
    ├─ 发送内容：
    │   ├─ system: P1_SYSTEM（事件记录员角色，客观陈述风格）
    │   └─ user: 上述组装好的完整Prompt
    │
    ├─ API请求参数：
    │   ├─ model: deepseek-v4-pro
    │   ├─ temperature: 0.7
    │   ├─ max_tokens: 4096
    │   └─ thinking: False（P1已关闭思考模式，节省token）
    │
    └─ AI返回JSON：
        ├─ narrative（叙事文本，纯事实陈述）
        ├─ player_state（更新后的玩家状态）
        │   ├─ 7个必填字段（位置、姿势、装备、健康、交通、天气、人物）
        │   └─ 3个可选字段（季节、时间、外貌）
        ├─ facts_delta[]（本轮新获知的事实数组）
        │   └─ 每项：{type, content, source}
        ├─ world_event（世界质变事件，日常为null）
        ├─ npc_notes{}（NPC心理/关系变化）
        │   └─ 每项：npc_id → 变化描述
        └─ new_entities[]（新实体：地点/NPC/物品）
            └─ 每项：{type, name, description}
    ↓
解析AI返回的JSON
```

### 4.3 状态验证与修复

```
提取 player_state 进行字段完整性检查（_check_state_fields）
    ↓
检查7个必填字段是否都存在且非空：
    ├─ current_location
    ├─ posture_action
    ├─ clothing_equipment
    ├─ physical_health
    ├─ transportation
    ├─ weather_environment
    └─ current_scene_people
    ↓
如果发现缺失字段：
    ├─ 构建重试Prompt：告知AI哪些字段为空，要求基于当前叙事补全
    ├─ 再次调用主力AI（temperature=0.5, thinking=False）
    ├─ 如果重试成功：用返回的值填充缺失字段
    └─ 如果重试失败：使用默认值（未知地点/站立/普通衣物/健康/无/晴朗/独自一人）
    ↓
状态验证完成
```

### 4.4 游戏状态更新

> 以下所有操作都在 `GameWindow._process_left_input()` 的后台线程中顺序执行。

#### 4.4.1 更新玩家状态

```
GameState.update_player_state(new_state)
    └─ 作用：把AI返回的 player_state 覆盖到内存中
    └─ 文件位置：src/game_state.py 第81-83行
    └─ 做了什么：self.player_state = new_state
    └─ 注意：此时还没写入硬盘，只是内存更新
```

#### 4.4.2 记录本轮交互

```
GameState.record_round(player_input, narrative, ai_output)
    ├─ 作用：把本轮的玩家输入、AI叙事、完整AI输出打包存入历史
    ├─ 文件位置：src/game_state.py 第100-110行
    ├─ 做了什么：
    │   ├─ current_round += 1（轮次+1）
    │   └─ action_history.append({
    │           round: 当前轮次,
    │           input: 玩家输入,
    │           narrative: AI叙事文本,
    │           ai_output: AI返回的完整JSON,
    │           timestamp: null
    │       })
    └─ 结果：玩家可以在游戏中查看"接上次的冒险"历史记录
```

#### 4.4.3 处理新获知的事实（facts_delta）

```
遍历AI返回的 facts_delta 数组中的每条事实：
    └─ GameState.add_fact(fact_data)
        ├─ 作用：把本轮新发现的事实加入已知信息库
        ├─ 文件位置：src/game_state.py 第73-79行
        ├─ 做了什么：
        │   ├─ known_facts.append(fact_data)  ← 加入总事实列表（P1下次会全部收到）
        │   └─ pending_facts.append(fact_data)  ← 标记为"待保存"（等save_all时写入硬盘）
        └─ 结果：玩家以后用系统助手查询时，能查到这些事实
```

#### 4.4.4 处理NPC心理变化（npc_notes）

```
遍历AI返回的 npc_notes 字典中的每个NPC：
    └─ 如果该NPC存在于当前存档中：
        ├─ 构建P4 Prompt（build_p4_user）
        │   ├─ 输入：NPC完整档案 + **所有**心理日志 + 本轮事件描述
        │   └─ 目的：让AI深度分析这个事件对NPC心理的影响
        │
        ├─ 调用主力AI（P4_SYSTEM, thinking=False）
        │   └─ 返回JSON：psychology_change（心理变化）、trait_shift（特质偏移）、future_behavior_hint（未来行为暗示）
        │
        └─ GameState.add_npc_psychology(npc_id, entry)
            ├─ 作用：把AI的心理分析结果追加到该NPC的心理日志中
            ├─ 文件位置：src/game_state.py 第92-98行
            └─ 做了什么：npcs[npc_id]["psychology_log"].append(entry)
                └─ entry 包含：轮次、事件描述、P4分析结果
```

> **通俗解释**：AI每轮会告诉你"NPC A 对你产生了怀疑"，然后程序会单独再调用一次AI（P4），让AI基于这个NPC的性格和过往经历，写出更详细的心理分析（比如"信任度下降，未来可能会试探玩家"）。这些分析会存进NPC档案，影响后续叙事中该NPC的行为。

#### 4.4.5 异步提取额外事实（P3）

```
（在独立后台线程中执行，不阻塞主流程）

self._call_p3_async(narrative)
    ├─ 作用：用轻量模型从叙事文本中自动提取玩家可能忽略的事实
    ├─ 文件位置：src/ui/game_window.py 第408-424行
    ├─ 做了什么：
    │   ├─ 构建P3 Prompt：把本轮叙事文本传给轻量AI
    │   ├─ 调用轻量AI（call_lightweight, temperature=0.3, thinking=False）
    │   └─ 解析返回的JSON事实数组
    │       └─ 每条事实 → GameState.add_fact() 加入 known_facts
    └─ 结果：自动补充一些AI叙事中隐含但玩家没明确"获知"的信息
```

> **通俗解释**：P1负责写故事，P3负责"做笔记"——从故事里自动摘出重要信息（比如"NPC提到了国王的名字""墙上有一幅奇怪的地图"），这些笔记以后玩家查询时都能查到。

---

### 4.5 自动保存

```
GameState.save_all()
    ├─ save_manager.save_player_state(player_state) → player_state.json
    ├─ save_manager.save_known_facts(known_facts) → known_facts.json
    ├─ save_manager.save_action_history(action_history) → action_history.json
    ├─ 遍历所有NPC：
    │   └─ save_manager.save_npc(npc_id, data) → npcs/npc_id.json
    ├─ save_manager.update_meta(
    │       rounds=current_round,
    │       player_name=player_profile["name"]
    │   ) → meta.json（同时更新last_played）
    │
    └─ 检查归档触发：
        ├─ action_history 长度 >= 250？
        │   └─ 是：archive_old_history(100)
        │       ├─ 取前100轮压缩为gzip
        │       ├─ 保存到 archives/action_history_0001_0100.json.gz
        │       └─ 剩余历史写回 action_history.json
        └─ 否：跳过
    ↓
清空 pending_facts 和 pending_npc_updates
```

### 4.6 UI更新

```
回到主线程（root.after）
    ↓
GameWindow._update_after_left(narrative, player_state)
    ├─ 删除"[正在生成叙事...]"提示
    ├─ 在叙事区追加AI叙事文本
    ├─ 更新轮次显示（round_label）
    ├─ 更新位置显示（location_label）
    ├─ 更新状态栏（_update_status_bar）
    │   └─ 季节、时间、天气、位置、健康、物品、外貌、附近NPC
    └─ 重新启用输入框，聚焦
```

---

## 五、系统查询数据流（右列）

### 5.1 玩家查询

```
玩家在中列输入框输入查询，按回车或点击"查询"
    ↓
GameWindow.on_right_submit()
    ├─ 获取查询文本
    ├─ 清空输入框
    ├─ 在助手区显示玩家查询（带"你:"前缀）
    ├─ 显示"[正在检索...]"提示
    └─ 启动后台线程
    ↓
_process_right_input(query)
    ↓
判断查询类型：
    ├─ 人际关系关键词（关系/人际/朋友/敌人/好感/信任/认识谁/和谁/关系网/社交/人脉）
    │   └─ 是：构建人际关系上下文
    │       ├─ 遍历所有NPC
    │       ├─ 提取：名字、关系、角色、最近心理日志
    │       └─ 拼接成关系描述文本
    │
    └─ 否：普通事实查询
        └─ 无需额外上下文
    ↓
构建P2 Prompt（build_p2_user）：
    ├─ 已知信息库（所有known_facts的内容+来源）
    ├─ 玩家当前状态（位置、动作、装备、健康等）
    ├─ 最近5轮行动记录
    ├─ 玩家查询
    └─ （如果是人际关系查询）追加人际关系上下文
    ↓
调用主力AI（call_main, temperature=0.3, thinking=False）
    │
    ├─ 发送内容：
    │   ├─ system: P2_SYSTEM（系统助手，只能回答已知事实）
    │   └─ user: 上述组装好的Prompt
    │
    └─ AI返回纯文本答案
    ↓
回到主线程
    ├─ 删除"[正在检索...]"提示
    └─ 在助手区显示AI回答（带"助手:"前缀）
```

---

## 六、加载存档数据流

```
用户在主菜单点击"继续"或读取已有存档
    ↓
Application._start_game(save_name)
    ├─ 清除当前界面
    └─ 创建 GameWindow(root, save_name, on_return_menu)
        ↓
GameState.load() 读取所有存档数据
    ↓
show_initial_narrative()
    ├─ 显示主角简介、世界观、位置（同新游戏）
    └─ 如果 current_round > 0：
        └─ _show_last_rounds(3)
            ├─ 从 action_history 取最后3轮
            └─ 按格式显示：轮次标题 → 玩家输入 → 叙事文本
    ↓
_update_status_bar() 显示当前状态
    ↓
游戏就绪，等待玩家输入
```

---

## 七、设置变更数据流

```
用户点击"设置"按钮
    ↓
SettingsDialog 弹出（500×500）
    ↓
显示可配置项：
    ├─ API密钥（4个provider的输入框，密码显示）
    └─ 模型选择（主力模型 + 轻量模型的下拉框）
    ↓
用户修改后点击"保存"
    ↓
遍历所有输入框：
    ├─ 将API密钥写入 config.data["api_keys"][provider]
    ├─ 将模型选择写入 config.data["models"][type]["model"]
    └─ config.save() → ~/.ai_rpg_config.json
    ↓
显示"设置已保存"提示
```

---

## 八、叙事风格切换数据流

```
用户在游戏界面顶部的"叙事风格"下拉框选择新风格
    ↓
GameWindow.on_style_changed(event)
    ├─ 获取新风格值
    ├─ GameState.settings["narrative_style"] = 新风格
    ├─ save_manager.save_settings(settings) → settings.json
    └─ 在助手区显示"叙事风格已切换为：XXX"
    ↓
后续P1 Prompt中使用新的叙事风格值
```

---

## 九、返回主菜单数据流

```
用户点击"返回主菜单"
    ↓
弹出确认对话框（"当前进度已自动保存"）
    ↓
用户确认
    ↓
GameWindow.frame.destroy()
    ↓
Application.show_main_menu()
    ├─ 重新创建 MainMenu 实例
    └─ 刷新存档列表（显示最新状态）
```

---

## 十、错误处理数据流

### 10.1 AI调用失败

```
API调用返回错误（超时/连接失败/HTTP错误）
    ↓
重试机制（max_retries=1，共2次尝试）
    ↓
仍然失败：
    ├─ 返回错误信息文本 + success=False
    └─ 如果是JSON调用：返回 {"error": "错误信息"}
    ↓
回到主线程
    └─ _show_error(message)
        ├─ 删除"[正在生成叙事...]"
        └─ 在叙事区显示"【错误】XXX"
```

### 10.2 JSON解析失败

```
AI返回的内容不是合法JSON
    ↓
尝试从markdown代码块中提取
    ↓
仍然失败：
    ├─ 自动构建重试Prompt（要求纯JSON输出）
    ├─ 再次调用AI
    └─ 如果还是失败：返回错误 + 原始文本前2000字
    ↓
回到主线程显示错误
```

---

## 十一、数据持久化总览

| 数据 | 文件 | 更新时机 | 格式 |
|------|------|----------|------|
| 世界设定 | world_template.json | 创建时 | JSON |
| 主角档案 | player_profile.json | 创建时 | JSON |
| 玩家状态 | player_state.json | 每轮结束 | JSON |
| 已知事实 | known_facts.json | 每轮结束 | JSON数组 |
| 行动历史 | action_history.json | 每轮结束 | JSON数组 |
| 游戏设置 | settings.json | 创建时/切换风格 | JSON |
| NPC档案 | npcs/*.json | 每轮结束 | 每个NPC独立JSON |
| 元信息 | meta.json | 每轮结束 | JSON（轮次、时间） |
| 归档历史 | archives/*.json.gz | 超过250轮时 | gzip压缩JSON |
| 全局配置 | ~/.ai_rpg_config.json | 保存设置时 | JSON |

---

*本文档随游戏开发进度持续更新。*
