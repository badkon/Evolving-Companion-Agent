# Architecture

> Evolving-Companion-Agent 架构设计

本文档记录已经确定的架构原则与当前技术方向。

尚未确定的技术选择应明确标记，不将候选方案描述为最终决定。

## 当前工程状态 — A7.2 First-run Setup v0.1

项目使用 Python >= 3.12、`src` layout 和 `evolving_companion` 包，通过 setuptools 与标准 pip 安装。
A1 提供 CLI 和多轮内存会话；LLM 层通过 OpenAI Python SDK 调用 DeepSeek 的 OpenAI-compatible API。
A2 v0.1 将初始 Seed Character Data 独立存放在 `data/characters/si_001.yaml`，数据路径为 YAML → Pydantic validation → Character Projection → PromptBuilder → LLM。
SI-001 的 Character Seed Data 已包含固定 UUID `identity.internal_id`，作为后续 Character-owned runtime records 的稳定机器身份键；`development_id = SI-001`、人类可见名称与 `continuity_generation` 各自保持独立语义。Internal UUID 不投影到普通 Character Prompt。
当前 A3 冻结基线包含 SQLite Archive / Evidence、rule-based Memory Need 与 Selective Context、active-memory semantic retrieval、CrossEncoder rerank、Top-3 candidate injection、成功回复后的 Memory Extraction，以及保守的 `keep_both` / `supersede_old` / `uncertain` consolidation。详细流程、接受/延后决策、验证证据与已知限制统一见 [A3 Long-term Memory v1](a3_long_term_memory_v1.md)，避免在此重复维护阶段细节。
LLM Adapter 只从 `DEEPSEEK_API_KEY` 环境变量读取 API Key；CLI 与真实 LLM 实验入口可在启动时从 Git 忽略的项目根目录 `.env.local` 加载该变量，且不覆盖已存在的进程环境变量。sentence-transformers 已移至 `local-memory` optional extra；核心保留 NumPy 并直接依赖 HTTPX，开发依赖为 pytest 和 Ruff。

A6.0：Memory → EmbeddingProvider → Semantic Retrieval → RerankerProvider → Top-K Injection。默认 local/local 保留 CPU BGE；CLI / QQ 入口可显式配置 api/api 或 hybrid，API-only 不加载本地模型。Need Gate、Context Policy、semantic Top-10 → rerank → max Top-3、active-only 和无 threshold 均不变。memory_embeddings 的既有 model_name 列保存 provider/model/dimension 版本化描述符，旧缓存不误用，active 索引可显式/按需重建；权威 Memory/Evidence/Supersession 和 DB schema 不改变。A6.3 将 Conversation recall 失败隔离为本轮 no-memory context：记录仅异常类型的 developer diagnostic / warning，继续主 LLM，不注入错误或 fallback candidates；主回复成功后仍尝试 formation，主 LLM / Archive 失败边界不变。配置、HTTP contract、依赖与限制见 [A6 Memory Providers](a6_memory_retrieval_providers_v01.md) 和 [A6.2/A6.3 Server Availability Policy](a6_2_api_deployment_profile.md#7-failure-semantics)。

当前 A4 v0.1 已增加本地 SQLite 当前 Character State，使用 `identity.internal_id` UUID 作为 State 主键；A4.1 添加确定性 elapsed-time transition；A4.2 添加不持久化的显式 Character Event 及其 State Transition 映射。B1 增加统一可注入 UTC Clock，SQLite `character_runtime` 保存最后成功对话时间，Time Snapshot 转换到 Seed 指定的 Character timezone，并向 Prompt 投影本地日期、时间与离线时长。Conversation 的 State elapsed 与 Prompt 共用本轮快照时间。Archive / Memory 等历史存储时间戳仍由 storage 内部系统时钟写入，是当前保留的边界。详见 [A4 Character State / A4.1 State Transition](a4_character_state_v01.md)、[A4.2 Explicit Character Events](a4_2_character_events_v01.md) 与 [B1 Time Model](b1_time_model_v01.md)。当前没有 memory merge/summary；也没有 World/NPC 模拟或自主行为。完整 Character Store 和多设备服务仍属于架构方向，尚未实现。

## 1. 核心分层

A7.2 `si setup` / Manager Reconfigure 共用 SetupScreen → SetupService → DeploymentEnvService 与 A7 deploy_check、A7.1 SystemdServiceManager。只写白名单部署配置，masked key 默认保留、private backup/atomic replace、未知字段保留，路径/identity 只读；无 Character reset、DB schema/运行逻辑改变。离线检查在新本地进程复用现有入口，避开旧 file-derived 环境。Transport None 可以保存但不启动服务。详见 [A7.2 Setup](a7_2_first_run_setup_v01.md)。

A7.1 仅增加 Deployment/Operations UI：`si` → Textual SIManagerApp → DeploymentFacade / SystemdServiceManager → A7 helpers / systemd。配置只读白名单，secret 仅配置状态；恢复须确认并复用 A7 stopped/UUID/pre_restore 保护。阻塞操作经 worker thread，全部离线，不进入 Character Core、Prompt、Memory 或 transport 链。详见 [A7.1 SI Manager](a7_1_si_manager_tui_v01.md)。

A7 Deployment Layer 与 Character Core 分开：server entry 按 SI_CHAT_TRANSPORT 选择已有 QQ 入口，使用外部 `/opt/si/config/si.env` 和 `SI_RUNTIME_DB=/opt/si/runtime/si_001.db`，不读取开发 `.env.local`。SQLiteStore 的显式 path 优先；未配置时保持开发默认。systemd 以 si 用户运行，uv.lock 锁定 API-only 环境；离线检查不初始化 seed/runtime，不生成 UUID。备份/恢复使用 sqlite3 backup，恢复前要求停止服务并保护当前 DB。config/runtime/backups 不随 checkout 更新，Device Migration ≠ Character Reset。完整配置、复用评估、安全与验证限制见 [A7 Deployment Foundation](a7_deployment_foundation_v01.md)。A7.1 Manager 在独立运维层；未实现自动更新或新 Transport 框架。

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

B6：World/Life → ObservationService → 本轮 ObservationSnapshot → 独立 Prompt 环境区块。Character 位置只来自 Life current_location_entity_id，NPC 只按完全相同 Place + active 查询（稳定 UUID 排序，查询 21、最多返回 20）；不递归空间关系、不识别人物。Life 投影与观察共用 Store 的同一只读事务，事务在 LLM 前关闭；CLI/QQ 共用 Clock 和 identity.internal_id。仅匿名人物存在信息与已记录地点进入 Prompt，文本被引用为数据；无缓存或观察持久化。查询失败省略本轮组合上下文，只记录异常类型，主完成及 formation 边界不变。Observation ≠ Knowledge ≠ Experience ≠ Memory；assistant 提及观察后的 Archive 仍只是 Conversation evidence，并非完整 World evidence。intention → Action Resolver → World 仍未实现。详见 [B6 Observation Layer](b6_observation_layer_v01.md)。

B2 复用 `apply_elapsed_time()`，依据 State 中四个独立 UTC 字段锚点进行 baseline reconciliation。`updated_at` 表示整行最后实际变化时间；无变化检查不刷新任何锚点，其他字段变化不重置未变字段的累计时间。SQLite 兼容初始化为旧行增加并回填四个锚点列。Offline Duration 使用 last interaction，State elapsed 使用各字段锚点，二者独立。活动保留，不生成离线经历。详见 [B2 Temporal State Reconciliation](b2_temporal_state_reconciliation_v01.md)。

其中：

- Client 负责人机交互；
- Character Core 负责认知、记忆、人格、关系、兴趣、世界等核心逻辑；
- Character Store 负责持久化 Character State。

各层之间应保持明确边界。

---

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
