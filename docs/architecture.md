# Architecture

> Evolving-Companion-Agent 架构设计

本文档记录已经确定的架构原则与当前技术方向。

尚未确定的技术选择应明确标记，不将候选方案描述为最终决定。

## 当前工程状态 — Runtime Configuration / Operations

Affective & Relationship State v1 扩展既有 Planner，同时产生 Appraisal 与 Guidance。
独立 SQLite 状态经确定性小步更新、lazy decay/recovery、每日绝对 cap，再投影给
Selector/Replyer；不改 Seed、World 或 Memory schema。QQ 授权后传稳定假名 UUID target，
关系/history 分离；无 ownership 的旧 Memory 仅对主要用户开放。
详见 [Affective & Relationship State v1](affective_relationship_state_v1.md)。

Natural Conversation Pipeline v1：正式 CLI / 共享 QQ-Core 工厂默认将原 PromptBuilder 上下文交给
ReplyPlanner → ReplyGuidance → ExpressionIntent → ExpressionSelector → Replyer。表达习惯和风格
独立于 Character Data，不修改 Memory/World。额外一次有预算的规划 LLM，最终归档、history 和
formation 完成边界不变；`SI_REPLY_PIPELINE=legacy` 保留原单次生成路径。
具体机制、失败降级、成本和 MaiBot 功能映射见 [Natural Conversation Pipeline v1](natural_conversation_pipeline_v1.md)。

[Persona Activation v1](persona_activation_v1.md) 在现有 CharacterProjector 中生成 Seed-derived
trait pool、核心边界与 Character Voice。自然路径本地预筛选最多 6 条候选，同一次
Planner/Appraisal 选择 0–3 个经校验的 trait ID；Replyer 仅接收已选语义，不接收整个偏好池。
优先级为事实边界、稳定立场、关系距离、当前情绪心情、表达习惯；traits 不写入任何长期状态。
legacy 仍使用原完整角色描述；未改 Seed、Memory/World schema、QQ 或 Runtime 生命周期。

[Simplified Conversation Pipeline v1](simplified_conversation_pipeline_v1.md) 是可配置的消融路径：
State → RelevantContextBuilder → Tiny Planner → Replyer。复用 Conversation 已取得的
Character/Memory/State/Time/Life/Observation，以及同一次规划中的 Appraisal 和既有状态事务；
不读取第二套状态、不改数据库 schema。Tiny 表达计划仅 focus/stance/boundary/ask。
`natural_simplified` 旁路 Expression Intent/Selector/Habits/TemporaryStyle；
`natural` 与 `natural_full` 保持完整模式，默认不变。自然度与真实 token/延迟待 QQ 验证。
Mini Life Context v0.1 只给 simplified 提供即时计算的 now/next/today：
复用 Seed 的 internal UUID、时区、喜好和既有 Life 关联；固定规则集中在 `mini_life.py`。
自由活动按 UUID/本地日期/时间块稳定选择，不调用模型、不写数据库或历史事件。
这是虚拟世界粗安排，不执行位置迁移；显式 State/Observation 优先，不证明过去经历。

项目使用 Python >= 3.12、`src` layout 和 `evolving_companion` 包，通过 setuptools 与标准 pip 安装。
A1 提供 CLI 和多轮内存会话；LLM 层通过 OpenAI Python SDK 调用 DeepSeek 的 OpenAI-compatible API。
A2 v0.1 将初始 Seed Character Data 独立存放在 `data/characters/si_001.yaml`，数据路径为 YAML → Pydantic validation → Character Projection → PromptBuilder → LLM。
SI-001 的 Character Seed Data 已包含固定 UUID `identity.internal_id`，作为后续 Character-owned runtime records 的稳定机器身份键；`development_id = SI-001`、人类可见名称与 `continuity_generation` 各自保持独立语义。Internal UUID 不投影到普通 Character Prompt。
当前 A3 冻结基线包含 SQLite Archive / Evidence、rule-based Memory Need 与 Selective Context、active-memory semantic retrieval、CrossEncoder rerank、Top-3 candidate injection、成功回复后的 Memory Extraction，以及保守的 `keep_both` / `supersede_old` / `uncertain` consolidation。详细流程、接受/延后决策、验证证据与已知限制统一见 [A3 Long-term Memory v1](a3_long_term_memory_v1.md)，避免在此重复维护阶段细节。
LLM Adapter 只从 `DEEPSEEK_API_KEY` 环境变量读取 API Key；CLI 与真实 LLM 实验入口可在启动时从 Git 忽略的项目根目录 `.env.local` 加载该变量，且不覆盖已存在的进程环境变量。sentence-transformers 已移至 `local-memory` optional extra；核心保留 NumPy 并直接依赖 HTTPX，开发依赖为 pytest 和 Ruff。

A6.0：Memory → EmbeddingProvider → Semantic Retrieval → RerankerProvider → Top-K Injection。默认 local/local 保留 CPU BGE；CLI / QQ 入口可显式配置 api/api 或 hybrid，API-only 不加载本地模型。Need Gate、Context Policy、semantic Top-10 → rerank → max Top-3、active-only 和无 threshold 均不变。memory_embeddings 的既有 model_name 列保存 provider/model/dimension 版本化描述符，旧缓存不误用，active 索引可显式/按需重建；权威 Memory/Evidence/Supersession 和 DB schema 不改变。A6.3 将 Conversation recall 失败隔离为本轮 no-memory context：记录仅异常类型的 developer diagnostic / warning，继续主 LLM，不注入错误或 fallback candidates；主回复成功后仍尝试 formation，主 LLM / Archive 失败边界不变。配置、HTTP contract、依赖与限制见 [A6 Memory Providers](a6_memory_retrieval_providers_v01.md) 和 [A6.2/A6.3 Server Availability Policy](a6_2_api_deployment_profile.md#7-failure-semantics)。

当前 A4 v0.1 已增加本地 SQLite 当前 Character State，使用 `identity.internal_id` UUID 作为 State 主键；A4.1 添加确定性 elapsed-time transition；A4.2 添加不持久化的显式 Character Event 及其 State Transition 映射。B1 增加统一可注入 UTC Clock，SQLite `character_runtime` 保存最后成功对话时间，Time Snapshot 转换到 Seed 指定的 Character timezone，并向 Prompt 投影本地日期、时间与离线时长。Conversation 的 State elapsed 与 Prompt 共用本轮快照时间。Archive / Memory 等历史存储时间戳仍由 storage 内部系统时钟写入，是当前保留的边界。详见 [A4 Character State / A4.1 State Transition](a4_character_state_v01.md)、[A4.2 Explicit Character Events](a4_2_character_events_v01.md) 与 [B1 Time Model](b1_time_model_v01.md)。当前没有 memory merge/summary；也没有 World/NPC 模拟或自主行为。完整 Character Store 和多设备服务仍属于架构方向，尚未实现。

## 1. 核心分层

SI Console v0.1 将 Web Setup 重组为九个认证页面：总览、聊天、媒体空入口、角色、情绪与关系、只读记忆、模型、运行与日志、数据与系统。复用现有 Starlette / Jinja / 原生 JS；内部 WebSetupServer / WebSetupService 名称保留。ConsoleService 仅做呈现和 SQLite `mode=ro` 查询；运行控制直接委托创建 Console 的同一个 ManagerService，沿用 NativeProcessController。无新 Core 初始化或 Memory/World 语义。入口 fragment token 换取临时 HttpOnly / SameSite=Strict cookie，多页导航不携带 URL token；cookie 写请求校验同源 Origin。详见 [SI Console](si_console_v01.md)。用户已报告上一版 Linux 安装/配置/API 测试/Core 控制通过；本轮 Console 与真实 QQ 私聊仍待验收。

情绪/关系 Console reader 按 Internal UUID 与已保存 primary target 只读查询，复用纯
Mood recovery / Emotion decay 计算，不调用 AffectiveStore 的写入初始化或 snapshot。
Dashboard 摘要与 `/affective` 详情页无新 LLM、schema、缓存或状态修改 API。

Docker Deployment v1 仅提供 Runtime Host：Python 3.12 slim image 在 build 时按 pyproject.toml 安装无 extras 的项目，通过 server 入口运行 api/api。server 按 transport 分派：qq 沿用既有 QQ runtime；none 使用共享 Core 初始化后以 asyncio.Event 等待常驻，SIGINT/SIGTERM 时退出并经 ExitStack 清理资源，不启动 Transport、聊天输入或后台轮询。Compose 单服务绑定宿主机 config/data（只读）、runtime/backups（可写），不改变 Character 生命周期；Linux host networking 连接外部 OneBot（qq 时）。healthcheck 复用 offline health，不依赖 provider/transport 网络。详细边界见 [Docker Server Deployment](docker_server_deployment_v1.md)，未加入新业务 runtime、发布或自动更新机制。

通用应用运维层与 Character Core 分开：`si` → SIManagerApp → ManagerService → RuntimeController，当前仅 NativeProcessController（Linux）。固定 argv 启动独立 `server`，使用既有 process identity/flock/pidfd，不在 Manager 内初始化 Core。Configure → 临时 loopback WebSetupServer → WebSetupService → ApplicationEnvService；`si setup` 保留原 TUI fallback。Web 复用 env/seed schema，锁定身份并原子保存；退出 Manager graceful 关闭 Web，不停止 Core。默认配置 `config/si.env`；显式 OS environment 优先，Manager 每次检查/启动 fresh 读取。普通 check 不调用远端 API；Web 只有显式 Test Connection 才请求原 API adapters / OneBot 状态。可选 SI_LLM_API_URL/model 仍走同一个 LLM adapter。server 的 qq/none 分派和共享 runtime.create_conversation 不变。最小 install.sh 只准备源码 venv/用户 launcher；无 update、其他 runtime backend、supervisor 或开机自启。Running/本地 Health 不等于聊天 Ready；旧版 Linux/API 验证有用户报告，本轮 Console 及真实 QQ 私聊仍待验收。详见 [Deployment UX v0.1](deployment_ux_v01.md)、[Manager](si_manager_v01.md) 与 [Runtime Operations](runtime_operations.md)。

[World Foundation v0.1 Closure](world_foundation_v01_closure.md) 冻结 B4 Places、B5 static NPC、B6 Observation、B7 typed move_to/Resolver 与 B8 Lazy World Time。NPC 普通更新在单个 SQLite 写事务读取最新行，只 patch 显式字段；Life 普通写入使用 initialize-if-missing / transactional partial patch，禁止用低层 full upsert 写回 stale snapshot。B7 幂等仅限当前 DB 保留的 receipt history，旧备份恢复可能移除回执。Observation 的 assistant Archive 复述仍不是完整 World evidence；不引入新来源或自动经历系统。

Setup 仅写白名单应用配置：masked key 默认保留、private backup/atomic replace、未知字段保留，路径/identity 只读。保存后在隔离本地进程检查，不启动服务；Transport None 可无密钥保存。SQLite backup/restore 保留完整数据库和身份校验，恢复前需要操作者停止全部数据库写入者并显式确认，先生成 pre_restore 备份；工具不管理外部进程。Device Migration ≠ Character Reset。

Conversation 的主回复完成边界为 user Archive、LLM 回复和 assistant Archive 均成功。assistant Archive 后先更新 in-memory history，再独立 best-effort 写入 last interaction、执行 Memory Formation / Consolidation；这些派生操作失败不影响已归档主回复。时间写入失败仅保留异常类型诊断 `last_interaction_error`，数据库锚点允许暂时滞后，不写虚假 fallback 时间。LLM 或 assistant Archive 失败仍中断本轮，不追加 completed history、不更新时间、不执行 formation。

项目遵循以下基本原则：

> Character ≠ LLM ≠ Client ≠ Device ≠ Body

Character 的身份与长期状态不应绑定于特定模型、操作系统、客户端或物理设备。

---

## 2. 基本结构

A5 保持同步协议边界；A5.1 增加 forward WebSocket transport（仅 Fake Server 验证）：

```text
QQ
↓
SnowLuma OneBot WS Server [external deployment]
↓
OneBotWebSocketTransport [async connection / echo / delivery]
↓
QQPrivateChatAdapter
↓ send(text)
Conversation Core
```

SnowLuma 不包含 Character logic；Adapter 仅验证 friend 私聊文本、allowlist、自身消息和有界内存去重，然后原样返回 Character response。QQ ID / event JSON 不进入 Core；无效或不允许事件不会 Archive，Core 失败不会被自动重跑。去重仅在同实例有限缓存内有效，必须逐轮调用。详见 [A5 QQ Private Alpha](a5_qq_private_alpha_v01.md)。

A5.1 transport 串行以 asyncio.to_thread 调用 Adapter，Store 各方法创建 / 关闭独立 SQLite connection，不跨线程共享常驻连接。仅发送 send_private_msg 普通文本动作并按 echo 关联 ack，有限重连保留 Adapter cache。delivery failure 不回滚 Core / Archive / History，不重跑 Conversation。入口 wiring 位于 qq_cli.py，未改变 Character Core 或 DB schema，未实现 SnowLuma 登录 / 启动。详见 [A5.1 OneBot Transport](a5_1_onebot_transport_v01.md)。

当前总体方向：

External / Internal Trigger
↓
Character Event
↓
Deterministic State Transition
↓
Current State Projection
↑
Authoritative Character Data + Relevant Long-term Memory
↓
PromptBuilder → LLM

Clock → Character Time Model
├─ Time Snapshot
├─ Offline Duration
├─ Passive State reconciliation (field-level anchors)
└─ Prompt Time Projection

Character State 按 `identity.internal_id` UUID 绑定；它与 `development_id`、Personality、Memory 和 World State 分离。

B3 的独立路径：Character Life Context → Prompt Context → PromptBuilder。CharacterLifeService 显式读取和部分更新当前生活上下文，使用相同的 `identity.internal_id` UUID。Seed 的 `initial_life_context` 只初始化缺失记录，不能覆盖已持久化值。时间推进不改变 Life Context；Location ≠ Activity，Life Context ≠ Memory ≠ World State。详见 [B3 Personal Life Scaffold](b3_personal_life_scaffold_v01.md)。

当前 Character Event → State；Character Event → Life Context 是未来扩展。本阶段不扩大专用于 State 的 Event payload/result，仅提供 LifeService 显式更新接口。

B4：World Seed → World Entity Store → World Entity Service → Character Life Context UUID references → Prompt Projection。Place 使用固定 World UUID，与 Character internal UUID 分离；canonical_name 可重名，仅精确唯一结果可解析。Life 的四个地点字段已改为 `*_entity_id`，启动时先初始化 World 再迁移旧 Life 字符串；未知值不猜测，旧文本保留在兼容列，新引用为空并返回诊断。World Truth ≠ Character Knowledge，description 和 parent 不自动投影。详见 [B4 World State](b4_world_state_v01.md)。

B5 增加独立 `world_npcs` 静态 Registry：NPCRecord → NPCService → SQLiteStore，仅显式创建/更新、读取 active 记录及 Place UUID 关联。Full Character 当前只有 SI-001；普通 NPC 是 Tier-0 lightweight World data，不是 Agent，无 LLM、Memory、模型、后台循环或自动移动。正式 NPC 初始化为 0，不虚构人物。World Truth ≠ Character Knowledge ≠ Character Observation；NPC 资料不直接进入 Prompt。详见 [B5 Lightweight NPC Registry](b5_lightweight_npc_registry_v01.md)。

B6：World/Life → ObservationService → 本轮 ObservationSnapshot → 独立 Prompt 环境区块。Character 位置只来自 Life current_location_entity_id，NPC 只按完全相同 Place + active 查询（稳定 UUID 排序，查询 21、最多返回 20）；不递归空间关系、不识别人物。Life 投影与观察共用 Store 的同一只读事务，事务在 LLM 前关闭；CLI/QQ 共用 Clock 和 identity.internal_id。仅匿名人物存在信息与已记录地点进入 Prompt，文本被引用为数据；无缓存或观察持久化。查询失败省略本轮组合上下文，只记录异常类型，主完成及 formation 边界不变。Observation ≠ Knowledge ≠ Experience ≠ Memory；assistant 提及观察后的 Archive 仍只是 Conversation evidence，并非完整 World evidence。详见 [B6 Observation Layer](b6_observation_layer_v01.md)。

B7：trusted explicit MoveToIntent → ActionResolver → SQLite BEGIN IMMEDIATE → Life location-only patch + terminal receipt → COMMIT → ActionResult。Character key 仍为 identity.internal_id，唯一位置仍在 Life；expected location 必须匹配最新行。world_action_results 持久化幂等键，同 ID 同请求重放原结果，不重新移动；不同请求冲突不覆盖。业务拒绝保存 receipt，storage/commit 失败保守区分 known rollback 与 outcome unknown。Life 普通 partial update 也在同一写事务读取最新行，仅更新显式字段；首次初始化仅补缺失。Intent ≠ Result ≠ World Fact ≠ Memory。没有聊天/LLM 动作解析、自主决策、NPC 行动、WorldEvent、State/Memory integration 或 Scheduler；B6 下一次读取自然刷新。详见 [B7 Action Resolver](b7_action_resolver_v01.md)。

B2 复用 `apply_elapsed_time()`，依据 State 中四个独立 UTC 字段锚点进行 baseline reconciliation。`updated_at` 表示整行最后实际变化时间；无变化检查不刷新任何锚点，其他字段变化不重置未变字段的累计时间。SQLite 兼容初始化为旧行增加并回填四个锚点列。Offline Duration 使用 last interaction，State elapsed 使用各字段锚点，二者独立。活动保留，不生成离线经历。详见 [B2 Temporal State Reconciliation](b2_temporal_state_reconciliation_v01.md)。

其中：

- Client 负责人机交互；
- Character Core 负责认知、记忆、人格、关系、兴趣、世界等核心逻辑；
- Character Store 负责持久化 Character State。

各层之间应保持明确边界。

---

B8：共享 Clock + Seed timezone → WorldTimeService → 瞬时时段 → B6 ObservationSnapshot.day_period → 既有环境 Prompt 区块。WorldTimeService 无 Store / 持久游标；未知位置仍可提供时段但不补全地点。Conversation 一轮读取一次 now_utc，B1/B2/B6/B8 共用；成功交流记账只在 assistant Archive 后执行，复用本轮时间、不另取完成墙钟。无新表、自动 Action、NPC schedule 或离线经历。详见 [B8 Lazy World Temporal Context](b8_lazy_world_time_v01.md)。

## 3. Character State

长期采用单一权威 Character State。

不同设备作为同一 Character 的访问入口，不分别维护独立人格。

当前阶段允许状态存储在本地。

未来出现 Windows + macOS 等多设备需求后，可迁移为：

Windows / macOS / Other Clients
↓
Character Server
↓
Character Store

当前不考虑离线多主同步。

---

## 4. 数据与代码分离

源代码与 Character Runtime Data 必须分离。

Character Runtime Data 包括但不限于：

- 对话；
- 记忆；
- 人格状态；
- 情感状态；
- 关系状态；
- 用户模型；
- 世界状态；
- 经历记录。

Runtime Data 不进入 Git。

---

## 5. 存储

当前使用 SQLite 保存对话原始记录、显式创建的 Memory，以及 A4 的单行当前 Character State。A4.1/A4.2 不新增 transition 或 event history 表；完整 Character Store 尚未实现。

B3 增加 `character_life_context`，每个 Internal UUID 一行当前生活上下文，不新增 history 表。读取和显式更新不创建 Memory，也不修改 Character State；普通 Prompt 不包含 UUID、时间戳或数据库字段名。

B4 新增 `world_entities`，parent 与 Life 的地点 UUID 使用 FK。Seed 插入只补充缺失 ID，不覆盖 runtime；当前没有动态 World State、world event history 或自动移动。

B5 新增 `world_npcs`，稳定 NPC UUID 主键、可空 Place FK、active 与基础资料。SQLite 地点检查要求引用存在且为 place，名称不是 identity；no-op 不刷新 updated_at，停用保留记录。没有 NPC history、Memory 或 scheduler，不修改 Character-owned 表的语义。

B6 不新增表、索引、migration 或 runtime 文件；只读 Life/World/NPC，不修改 State 或 Memory。ObservationSnapshot 仅属于当前 turn，不保存到 history 或 evidence，也不恢复上一轮快照。

B7 新增 world_action_results，仅保存 success/rejected move_to 回执及原请求字段；目标 UUID 无 existence FK，以容纳 invalid_destination 拒绝。位置与 success receipt 原子提交，没有 pending/job/history framework；回执不是当前 location 或 Memory evidence。失败不自动重试。

上层模块不应直接依赖具体数据库实现，应通过统一的数据访问边界访问 Character State。

这样未来可以在不重写核心逻辑的情况下迁移到 Character Server 或其他存储方案。

---

## 6. 技术方向

当前倾向：

- Character Core：Python；
- Character Store：SQLite；
- Client：待聊天底座选型后确定；
- Desktop：未来考虑 TypeScript 与 Tauri 等方案；
- Server：未来优先考虑 Linux。

以上部分技术选型仍可能随着原型验证而调整。

---

## 7. 当前工程原则

当前阶段：

1. 优先复用成熟的基础聊天能力；
2. 不重复实现与 Character 核心无关的成熟基础设施；
3. 不提前实现尚未出现实际需求的复杂系统；
4. 为未来多设备与服务器迁移保留清晰边界；
5. Character 核心状态应尽可能与具体平台无关。
