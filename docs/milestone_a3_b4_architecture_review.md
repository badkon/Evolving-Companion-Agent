# A3–B4 Milestone Architecture Review

## 1. Scope

审查日期：2026-10-01。范围为当前 SI-001 的 Identity、A3 Memory、A4 State / Events、B1 Time、B2 Reconciliation、B3 Life Context、B4 World Entity，以及 Prompt、Conversation、SQLite schema 和兼容迁移。

本次是只读代码与设计审查，不修改生产实现、schema、fixture 或测试，不运行真实 LLM / embedding benchmark，不读取私人 runtime 数据库或 secrets。结论依据源码、现有测试及设计文档；不把静态规则或少量历史 smoke 当成自然语言质量的泛化证明。

主要依据：`README.md`、`CONTRIBUTING.md`、`docs/architecture.md`、Character Constitution / Identity / System Governance、Character Design、A3 closure、A4 / A4.2、B1–B4 和 World Design 文档；`src/evolving_companion/` 中对应实现及相关 tests。历史 B3 名称引用设计以当前 B4 UUID 引用实现为准。

## 2. Current Architecture

```text
Character YAML → Pydantic Seed → CharacterProjector → Character Context
                                                     ↓
State + Time + Life/World display names + Memory candidates → PromptBuilder
                                                     ↓
Current in-memory history + user message → LLM → assistant text

Conversation Archive → Extraction → evidence-backed Memory
                                 → Consolidation → status / supersession links
Active Memory → rebuildable embedding cache → Retrieval → Rerank → recall candidates

Explicit Character Event → validated mapping → State Transition
Clock passage → field-anchor reconciliation → State
World Seed → stable Place identities ← Life Context UUID references
```

当前是单 Character、同步 CLI、单 SQLite 文件架构。`SQLiteStore` 同时承担多类小规模持久化职责，尚未形成通用服务框架；现阶段没有证据要求为目录美观拆成 Repository / DAO 层。

World Entity 是地点 identity layer，不是 World Simulator。角色输出没有执行 World action 的接口，World description 不默认注入 Prompt。

## 3. Identity

- `character_data.py:IdentityData` 分开定义 `internal_id: UUID`、development code、人类可见名称和 continuity generation；SeedModel frozen，普通 runtime 没有 identity 修改 API。
- 当前 seed 固定 UUID 为 `dccb85ed-04ce-4d8f-a135-f60387eaad9e`，开发代号仍为 `SI-001`，工作名为玲，正式姓名未定。加载及数据库初始化不重新生成 UUID。
- `character_state`、`character_runtime`、`character_life_context` 使用规范 UUID 字符串作为 `character_id`，CLI 从 `identity.internal_id` 传入；未发现 development code 被用作这些表的主键。
- CharacterProjector 不把 internal UUID、development code 或 continuity generation 投影到普通聊天上下文。
- **不是所有持久化数据都已有 Character ownership**：Archive / Memory / embedding / supersession 依赖单角色数据库边界，没有 Character owner 字段。第二角色不能直接共享同一文件，否则 recall、去重和 consolidation 会混用数据，见 F07。
- 当前迁移保留已有 Character key，未发现替换 identity 的代码。跨设备保持 identity 的前提是迁移同一 seed 和 runtime 数据，而非创建新的 seed；当前没有完整迁移工具或跨文件 identity 校验。

现有 SI-001 正常路径的稳定身份：**no issue found**。多角色与跨设备工具属于明确后续边界，不应据此声称已支持。

## 4. Memory

### 已保持的边界

`conversation.py:send` 先 recall，主回复成功归档后才 formation。因此本轮新形成的 Memory 不会反向进入本轮 Prompt。LLM 抛错时只留下 user Archive，不进入 formation。State / Event / Life / World API 不自动调用 extraction，也不把 system record 直接转换为 Character memory。

`memory_retrieval.py:retrieve` 通过 `list_active_memories()` 取候选；`storage.py` SQL 限制 `status = 'active'`。顺序执行的生产路径中，superseded / archived Memory 不参与 retrieval。旧内容仍可能留在本轮 in-memory history，这是短期上下文，不是 active-only recall 失效。

Embedding 是 `(memory_id, model_name)` cache：768 维 float32 BLOB，坏向量 / 模型不匹配可重建。Memory 内容仍是源数据，向量不是 Character knowledge，也不是独立 memory。当前无内容编辑 API，因此没有现行“修改内容却复用旧 cache”的正常路径；未来编辑 / 模型 revision 需考虑 cache invalidation。

### Evidence 与 supersession

`persist_saved_candidates` 仅保存 save、非空且去重的 evidence refs，并要求 refs 属于传入 chunk。`create_memory` 检查 Archive ID 存在，Memory 与对应 evidence 在同一事务写入；memory_id 有 FK，正常 API 不产生孤立 Memory evidence。

但传入 chunk 的 role/content 没有与 Archive 原文重新核对，通用 `evidence_ref` 在 SQLite 中没有指向 Archive 的 FK。正常 Conversation 自己构造正确 chunk；外部错误调用或直接 SQL 不具备同等保证，见 F02。重复内容去重会跳过追加本次 evidence，不破坏旧证据，但也不累计重复确认。

Consolidation 全部 judge 完成后才事务 supersede；失败保留已保存的新 active Memory，不提前更改旧状态。历史证据与旧记录未删除。生产链使用 `memory_supersessions`，旧 `memories.supersedes_memory_id` 仍可由另一 API 独立填写，见 F03。候选“old”只检查 active，不检查时间方向；同一批先全部保存再逐条处理，可能把后保存记录当 old，见 F04。

## 5. State

State 的 energy / attention / mood_tendency / social_engagement 是有限短期值，current_activity 是可空活动文本。没有普通对话语义推断更新器，LLM 返回内容不会改 State。

Prompt 明确将 State 作为弱表达倾向，不替代人格、不定义关系亲密度。Life 地点有独立 UUID 字段；没有代码自动把 current_location 填入 activity。活动文本本身没有本体分类器，开发者仍可输入地点类词语，这不等于已经实现位置推断。

Explicit Event 校验 Character UUID、事件类型、来源和 payload，然后映射到 Transition；passive reconciliation 独立处理时间流逝。activity-ended 对活动名称匹配有保护，时间流逝不自动清除 activity。没有 Event persistence / replay / activity session identity，不能作为未来可靠事件日志使用。

当前 Personality / State / Life 职责分离：**no issue found**。未来系统不能将 social_engagement 当 relationship closeness，也不能把活动字符串升级为离线生活事实。

## 6. Time

| 字段 | 当前语义 |
| --- | --- |
| energy_updated_at / attention_updated_at / mood_updated_at / social_updated_at | 相应字段实际变化的时间锚点 |
| state.updated_at | 当前 State 快照最后实际变化时间；不作为所有字段的被动 elapsed 起点 |
| last_interaction_at | 已成功生成并归档的对话之后记录的交互时间 |
| event.occurred_at | 显式事件时间；通过验证后用于对应 State transition |
| offline_duration | 距上次成功交互的间隔，不是实际进程停机时长，也不是离线活动 |

`character_state_transition.py:apply_elapsed_time` 从各 field anchor 算 elapsed。没有变化不写库、不刷新 anchor；显式变化只刷新真正改变的字段 anchor。当前 energy 等默认化阈值是离散规则，不是连续模拟。

now 早于 state.updated_at 时保留全部字段和时间，返回 `clock_moved_backwards`。last-interaction 写入亦保持单调。未发现“改变 mood 顺带重置 energy 计时”的正常路径；相关测试覆盖独立锚点、no-op、rollback 和恢复后的 elapsed。

Conversation 用同一个 pre-LLM now 做 reconciliation 与 Time snapshot；完成回复后重新取 now 记录交互，这两次采样分别代表开始快照与完成时间，不是无意的不一致。

限制：Archive / Memory 创建时间仍使用 Store 自己的系统 UTC clock，不能声称所有持久化时间都服从注入 Clock。事件允许小幅未来时间（当前容差五分钟），可能暂时把 State anchor 推到 conversation now 之后；之后正常对话走 rollback diagnostic 而不回退。它不写 last_interaction，但这种容差不能直接沿用为 World simulation 的时间推进政策，见 F10。

## 7. Life Context

Life Context 保存 life_stage、current_role 和 home / school / primary_area / current_location Entity UUID；这是 Character↔World 关联，不是 State、人格或 episodic Memory。

`character_life.py:CharacterLifeService` 仅在 runtime 行不存在时从初始 seed bootstrap。partial update 使用 UNCHANGED 区分省略与显式 None；None 清空字段，no-op 不刷新时间。候选地点存在性检查先于写入，旧 timestamp 更新被拒绝。时间流逝和用户自然语言都不自动确定地点。

Projection 只解析关联地点的 canonical display name，不引入 description、parent graph、课程、天气、人物或经历。当前 role 也是生活身份描述，不替换 Character identity。

当前 CLI 全部服务共用一个 Store，初始化顺序正确。Conversation 却每轮从自身 Archive Store 构造 WorldEntityService，而 Life Service 可以独立持有另一 Store；这是隐含共库契约，且 Life / World resolution 异常直接中断主回复，见 F09。

## 8. World

`world.py` 定义稳定 UUID 的 Place、可重复 canonical name、可空 parent 和 description。精确名字解析遇到多义拒绝并要求 UUID，不把名字当机器 identity。parent 仅表示 containment / belonging，没有 adjacency、行动许可或路线语义。

WorldSeedData 拒绝重复 UUID、缺失 parent 和循环包含；SQLite FK 保证 parent 存在并禁止直接自指。跨多节点环的完整校验目前只发生在 seed 验证，不是任意未来 World update 的通用约束。

`insert_world_seed` 冲突 UUID 时 DO NOTHING，不覆盖 runtime 行。B4 seed 仅有四个最小地点；World Design 中未落入 seed 的空间只是设计规划，不是已可操作世界。

普通 Character / LLM 没有 World DB 写能力；本地 developer 命令只显式更新 Life 引用。没有把 World description 默认变为 Character knowledge 的路径。

World identity layer 本身：**no issue found**。旧 Life 名称迁移只用本次 seed 的精确名字映射，需要注意完整 seed 假设，见 F05。

## 9. Prompt Composition

实际 system 拼装顺序为：System Instructions → Character Projection → State → Memory candidates → Time → Life Context；之后按序追加全部 in-memory user/assistant history 和当前 user message。

已确认：

- Character、State、Time、Life 和 Memory 的 identity UUID、DB 行元数据、内部 updated_at / field anchors 不通过这些结构化投影输出。
- Time 显示的本地日期 / 当前时间属于有意提供的时间语义，不是泄漏内部 persistence timestamp。
- Memory 仅显示 content 与 memory_type / source，明确为可忽略候选，不是世界真相、当前消息或 Character Data。
- State 不替代 Personality；Life 不构成 lived memory；Time 不构成离线亲历。
- World description 与 parent 不默认投影。保留三条中性 few-shot 与自然变化、选择性注意规则，没有固定气泡或硬截断。

“UUID 不泄漏”限于不投影机器身份字段；如果用户输入 / Memory content 自身包含 UUID，当前没有文本清洗器，不能承诺任意内容绝不含 UUID。

System 中认知边界有一定重复，是不同上下文的防误读提示，未发现语义冲突。当前没有实测 token / latency 数据证明静态 system 已过长，不建议近期大规模重构 Prompt。更实质的限制是 history 无界，以及 raw Memory / role / activity / display-name 文本嵌入同一 system message 的指令信任边界，见 F06、F11。

## 10. Conversation Orchestration

源码 `conversation.py:send` 的真实顺序：

```text
Archive user（独立提交）
↓ pre-LLM Clock.now_utc
State temporal reconciliation（可写 State）
↓
Time snapshot（使用同一 now）
↓
Life bootstrap/read + World display-name resolution
↓
Memory recall（旧 history + 当前 user）
↓
Prompt → 主 LLM
↓
Archive assistant（独立提交）
更新 in-memory history
↓
completion Clock.now_utc → best-effort last_interaction
↓
Memory formation：extract → save evidence-backed candidates
↓
逐条 consolidation（同步）
↓
return reply → CLI 打印
```

主 LLM 失败不归档 assistant、不更新 history、不形成 Memory。此前 user Archive、真实时间导致的 State 归一、首次 Life bootstrap 可以已经提交；它们不是根据失败回复制造的亲历记忆。

F01 已 **PATCHED / RESOLVED**：assistant Archive 成功后先更新 history；last-interaction 写入失败仅记录异常类型到 `last_interaction_error`，不撤销 Archive、不伪造 timestamp、不阻止 formation 或 reply return。数据库 last-interaction 允许暂时滞后。主 LLM / assistant Archive 失败仍抛错，不进入这些完成后操作。

修复前审查曾用内存 fake 复现 `OSError`、两条 Archive 和空 history；这是历史失败路径证据，不是当前行为或真实 runtime 损坏记录。修复测试使用临时 SQLite、FixedClock 和 fake LLM / formation，覆盖时间写入失败后回复、历史和下一轮连续性，以及原锚点保留、formation 独立容错和 assistant Archive 失败。

Recall / reranker / Life / World 故障也可能发生在主 LLM 前；目前没有降级策略。同步 formation / consolidation 在 CLI 打印前执行，所以“回复先完成”不等于“用户先看到回复”。

## 11. SQLite / Migration

当前实际核心表共九张：

| 表 | 主键 / 引用与职责 |
| --- | --- |
| archive_messages | 消息 UUID；conversation UUID；user / assistant 原文与 UTC 创建时间 |
| memories | Memory UUID；枚举 CHECK；旧 supersedes_memory_id 自引用 FK |
| memory_evidence | memory_id / evidence_kind / evidence_ref 复合 PK；memory FK；通用 evidence_ref 由 API 检查 Archive |
| memory_embeddings | memory_id / model_name 复合 PK；memory FK；维度、BLOB、cache 时间 |
| memory_supersessions | old / new Memory 关联；Memory FK；生产 supersede 历史 |
| character_state | Character internal UUID 主键；短期字段与独立 UTC anchors |
| character_runtime | Character internal UUID 主键；last_interaction 等 runtime 时间 |
| character_life_context | Character internal UUID 主键；四个 world FK；保留旧 reference 和迁移 marker |
| world_entities | Entity UUID 主键；parent 自引用 FK；地点内容及 UTC 时间 |

Character / World 的 UUID 对象写入时转成规范字符串，程序生成的消息 / Memory UUID 也是规范字符串。接受外部字符串的 Store API 验证 UUID，但不统一重写其大小写 / 表达形式；SQLite TEXT 不独立保证 UUID 格式。当前内部生成路径一致，未来 import 应统一规范化。连接启用 foreign_keys；方法使用 `closing` 释放连接，没有长期打开的 Store 连接需要另行关闭。

Schema 初始化采用 IF NOT EXISTS；anchor / Life 列补齐在显式事务中，并保留原列。旧 State 缺失 anchor 从原 updated_at backfill，保守地承认没有更早的逐字段历史。无 DROP / 清空 Archive / 更换 Character key 的迁移。

CLI 顺序：Store schema → World seed 插入 → legacy Life name migration → State / Life / Time 服务 → Conversation。World parent FK 支持 seed 同事务插入；Life 新引用写入之前 World 行已存在。

Schema 初始化、World seed 插入、Life 数据迁移是不同提交阶段，不是整个启动的单一原子事务。中断后可重试 schema 和 seed；Life 转换事务失败可回滚。无法解析的旧名称保留在 legacy 原列，新 UUID 为 None，记录诊断并标 migrated；不会在以后 seed 扩展时自动再试。没有 raw-data 删除，但需要人工确认 unresolved 引用，不应将 migrated marker 理解为所有引用完整。

尚无 versioned migration / backup / 多角色 ownership migration。本阶段不必立刻引入框架；后续非加列式变更应先明确版本与恢复策略。

## 12. Cross-module Boundaries

| 边界 | 当前结果 |
| --- | --- |
| Personality vs State | 分别来自 Seed Projection 和短期 runtime；Prompt 明确不替换人格 |
| State vs Life | activity 与 Place UUID 分开；无时间自动位置推断 |
| Life vs World | Life 存 Character 引用，World 存地点 identity；没有把 Life 当地点全局状态 |
| World Truth vs Character Knowledge | 当前只投影自身关联 display name，非 description / 全库事实；未来 Observation 尚缺 |
| Knowledge vs Memory | System 明确模型知识不等于角色知识，Memory 为候选，不默认亲历 |
| Event vs Transition | 事件验证 / 映射与字段变化规则分开；事件不是持久日志 |
| Time Passage vs Event | 被动归一不制造发生过的 Event |
| Offline Duration vs Activity | 只展示交互间隔，不生成离线行程或经历 |
| System Record vs Lived Experience | Archive / runtime / seed 不自动等于亲历；Memory 仍依赖 evidence 与语义 gate |

没有发现当前正常路径主动破坏这些本体边界。Prompt 约束不能保证任意模型输出永不越界；后续 Action / Observation 不应仅依靠自然语言声明实现权限隔离。

## 13. World Simulation Readiness

1. **WorldEntity 可继续作为 identity layer**，无需因未来模拟回滚 B4。
2. **未来需要独立 WorldState**，不要把动态天气、物体状态、人物活动都塞进 Place description。
3. **Life Context 适合作为 Character↔World 引用层**；当前位置的变化将需要明确授权 / 成功 action 结果，而不是 conversation 猜测。
4. Character 的语言输出没有直接 World persistence 写接口；但 Conversation 已具体依赖 WorldEntityService 做名字解析，Life 与 World 共 Store 是隐含契约，见 F09。
5. **需要 Observation Layer**，区分全局 truth 与角色可见 / 已知事实。当前 display-name projection 不等于已具备感知系统。
6. **需要 Action Resolver**，校验 intent、权限与世界结果；不能让 LLM 直接更新 SQL / Life / State。
7. PromptBuilder、MemoryExtractor、Clock、passive State reconciliation、Life Context 和当前 CharacterEventService 都不应承担世界模拟。未来模拟产生的事实也不能无条件成为亲历 Memory。

结论：具备继续设计 World Simulation 的最小 identity / binding 基础；**尚不具备运行 World Simulation 的能力**。F01 已修复；后续仍需定义 Observation / Action 的边界，不要求现在实现这些模块。

## 14. Findings

P0：当前数据 / 身份可能损坏；P1：继续开发前应修；P2：可后续修；P3：已知限制 / future work。P2 中条件性风险不代表已经在真实数据库中观察到错误。

| ID | Severity | Area | Finding | Evidence | Recommended Action |
| --- | --- | --- | --- | --- | --- |
| F01 | P1 — PATCHED / RESOLVED | Conversation completion | 原 last-interaction 故障会阻止 history / return；现以 assistant Archive 成功为完成边界，先更新 history，后独立尝试时间写入与 formation | `conversation.py:send` / `last_interaction_error`；`tests/test_conversation.py` 临时数据库失败路径与下一轮测试 | 已完成局部 patch；不引入大事务、重试队列或后台 worker |
| F02 | P2 | Evidence integrity | chunk role/content 未核对 Archive 原文；evidence_ref 无 Archive FK。错误外部调用可以把真实 ID 与错误文本关联 | `memory_formation.py:process_turn`；`memory_extraction.py:persist_saved_candidates`；`storage.py:create_memory/add_evidence/SCHEMA` | 外部导入或复用 formation 前明确可信输入边界、核对 Archive；直接 SQL / 删除能力引入前补证据引用保护 |
| F03 | P2 | Supersession representation | `memories.supersedes_memory_id` 与 `memory_supersessions` 双重表达不联动；前者不改变旧 status，生产后者不填写前者 | `storage.py:create_memory`、`supersede_memories`、`get_superseded_memories`；现有单引用测试保留旧 active | 文档和 API 明确 canonical supersession 表与 legacy 字段含义，后续查询不要混用两者 |
| F04 | P2 | Consolidation direction | candidate “old”没有时间方向约束；同批先保存全部再处理，可能把后保存 Memory 当旧记录，judge 输入仅 ID/content | `memory_formation.py:process_turn`；`memory_consolidation.py:process_new_memory` / judge | 后续补最小同批顺序验证并明确新旧判定；不能只把检索顺序当因果顺序 |
| F05 | P2 | Life migration | 初始化任意 seed 都会迁移所有未标记 Life 行，只用该 seed 名称；子集 seed 可能把可解析旧引用置 None 并永久标 migrated | `world.py:initialize_seed_entities`；`storage.py:migrate_life_references` | 显式声明 / 检查迁移需完整权威 seed，区分增量 seed 插入与一次性 legacy 转换；保留人工诊断 |
| F06 | P2 | Prompt trust boundary | raw Memory content、activity、role、display name 被插入 system；分节有语义提示，但没有明确把其中指令视为数据的统一边界 | `prompting.py:_build_memory_context/_build_state_context/_build_life_context` | 在接入外部内容 / World observations 前明确数据引用边界；当前只是结构风险，未做真实攻击输出验证 |
| F07 | P3 | Single-character ownership | Archive / Memory 不带 Character owner，Prompt 还固定玲 / Azusa；不能直接共享数据库运行 SI-002 | `storage.py:SCHEMA`、active 查询 / 去重；`prompting.py` | 继续维持单角色隔离；多角色前单独做 ownership 设计和迁移，不借本审查实现 |
| F08 | P3 | Evidence accumulation | active 内容去重直接 skip，重复确认不追加新证据；原证据仍保留 | `memory_extraction.py:persist_saved_candidates` | 接受 v1 去重限制；将来若利用频次 / 证据强度，再设计累计规则 |
| F09 | P3 | Context availability / coupling | Conversation 用 Archive Store 创建 World resolver，Life 可以是另一 Store；read / recall 失败直接中断主回复。当前 CLI 同库正常 | `conversation.py:send`；`character_life.py:project_context`；`cli.py:main` | 保持并记录同 Store 契约；未来替换持久化 / 接入模拟时决定哪些 context failure 可降级，避免静默制造地点 |
| F10 | P3 | Clock scope | Archive / Memory 使用系统 UTC，非注入 Clock；允许近未来 event 暂时推进 State anchor | `storage.py:_utc_now`；`character_events.py` 时间容差；`time_model.py` | 接受当前单机时间边界；模拟时单独决定事件调度及记录 clock，不把容差当时间旅行 |
| F11 | P3 | Operational / semantic limits | history 无界；recall 无 relevance threshold；formation / 多次 judge 同步阻塞 return；语义质量只有有限 smoke evidence | `conversation.py`、`memory_recall.py`、`memory_formation.py`、A3 closure | 作为已接受限制记录，长会话前评估上下文与延迟；不在本次加 summary、threshold 或 worker |
| F12 | P3 | Simulation / concurrency | 当前只有静态 Place identity，无 Observation、Action Resolver、事件 replay 或多写者一致性保障 | `world.py`、`character_events.py`、Store 读后写 API | 后续独立定义世界动态与权限边界；不让当前小模块隐式升级为 simulator |

原审查统计：**P0 = 0，P1 = 1，P2 = 5，P3 = 6**。P1 中 F01 已 resolved，当前未解决 P1 为 **0**；其他发现未在本次 patch 中处理。没有观察到当前真实 runtime identity / Archive 损坏，不以未来共享库或外部错误调用推断 P0。

## 15. Deferred / Accepted Limitations

本阶段可接受：单 Character 数据库、无自动 Archive 恢复 history、无 Memory v2 / summary、无 Event replay、无多写者支持、有限真实 smoke、静态 World Place、未接 Observation / Action。

A3.5 已有真实 smoke evidence：Mac cancellation save + supersede_old + active-only recall；strategy / story keep_both；“今天不想喝咖啡”不形成长期 update。它们证明这些案例曾成功运行，不证明 consolidation 对所有自然语言冲突都可靠。

World Design 是人类设计依据，World Seed 是最小 runtime 初始化输入；规划中的地点不能因文档存在就算已运行。runtime 修改不由 seed 重覆盖，旧引用无法精确匹配时也不进行模糊猜测。

本次只新增 review 并加 README 链接。运行 Ruff lint、format check 与 git diff whitespace check；未新建测试、未运行真实 API / Hugging Face benchmark、未读写 `runtime/si_001.db`。pytest 为本任务可选，未重跑；引用现有测试仅说明已有覆盖，不宣称本轮完整测试通过。

## 16. Milestone Verdict

**READY WITH PATCHES**

A3–B4 的核心本体边界、稳定 Character / World identity、active-only Memory、逐字段 temporal anchors 和 Life UUID binding 足以保留并继续演进，不需要回滚或大规模重构。

原 verdict 保留为审查时的历史判断；其要求立即 patch 的 F01 现已 resolved，不再阻塞继续开发。P2 是后续数据接口与迁移需要认真处理的约束，尤其 F02 / F03 / F04 不应被未来工具误当已经严格保证；它们不要求当前实现 Memory v2。

可以继续 World Simulation 的架构设计，但不能声称当前系统已经能模拟世界。WorldEntity 保持 identity 层、Life 保持引用层，未来 Observation 和 Action Resolver 另行定义；本次没有实现任何未来模块。

## QQ Alpha P2 Triage

### 范围与判断条件

日期：2026-10-01。仅重新分类当前剩余 **F02–F06，共五个 P2**，不改变原 severity，不修生产代码、schema、Prompt 或迁移，不处理 P3。F01 的完成边界已 resolved。

按 SI-001、单 QQ 账号私聊、单进程、低频个人使用、DeepSeek / SQLite、可人工观察并允许重启判断，不以大型生产 SLA 或完美语义记忆为准。SnowLuma / OneBot 是拟接入入口，本次没有实现或验证 adapter。下面的 readiness 是剩余 P2 是否阻塞 Alpha 的判断，不是 QQ integration 已完成的声明。

沿用当前同步核心的逐轮调用、固定 internal UUID、固定完整 World seed 和同一 Store 初始化契约；“单进程”本身不保证未来异步 adapter 不并发调用。若引入并行 turn、其他用户 / Character、外部 Memory 导入、聊天触发开发命令或 World 写能力，应重新 triage，不能直接沿用此结论。

| Finding | Current Severity | QQ Alpha Category | Risk | Recommendation |
| --- | --- | --- | --- | --- |
| F02 — Evidence integrity | P2 | FIX SOON, NOT BLOCKING | 存在性 / chunk membership 不证明 candidate 的语义支持；正常自动 extraction 也可能接受内容错误但 refs 合法的 save。外部伪造 chunk 与直接 SQL 的结构风险在当前调用路径中不是常规触发源 | 尽快补针对不支持内容、assistant-only 用户事实、伪造 chunk 的反例评估，并明确结构验证与语义 gate 的保证边界；不要以增加 FK 或原文一致性检查冒充语义真实性证明 |
| F03 — Supersession representation | P2 | DEFER | 单引用字段与关系表不联动，但生产写状态和关系表、生产 recall 按 status 过滤；当前不依赖旧单引用字段作 recall 判据 | Alpha 继续只用现行 consolidation API，不新增单引用写法或混合查询；将 canonical 表 / legacy 字段统一留给后续独立兼容清理，不需要现在迁移 |
| F04 — Consolidation direction | P2 | FIX SOON, NOT BLOCKING | 同批多候选可进入互相比较，“old”不保证时间更早；可能错误选择 active 版本。这是当前可达 edge case，不是纯未来扩容问题 | 尽快补同批相反陈述 / 多候选顺序测试并定义比较 eligibility；不能简单把保存时间或 candidate 数组顺序当现实事件顺序。不要求 Alpha 前完成完整 temporal grounding |
| F05 — Life legacy migration | P2 | DEFER | 子集 seed 首次迁移会漏解析并永久标记；正常启动使用固定完整 seed，迁移不覆盖已有 UUID identity，旧文本保留 | 当前入口坚持完整 seed → migration → Life；观察 unresolved diagnostics。将子集初始化保护与旧值重新解析工具延后，不在 Alpha 入口做增量 World seed 初始化 |
| F06 — Prompt trust boundary | P2 | FIX SOON, NOT BLOCKING | Memory 文本进入 system 可能被误当指令，影响回复并间接污染后续 extraction；当前没有工具执行 / World SQL 写入，role/activity/地点名也不是普通聊天自动写入 | 尽快明确数据不是指令并做恶意文本反例检查；在接入不可信内容源或赋予行动权限前重新审查。不为此重写 Prompt composition 或引入安全框架 |

### F02：证据引用、原文一致性与语义支持分别判断

代码依据为 `memory_extraction.py:MemoryExtractor.extract_memories / persist_saved_candidates`、`memory_formation.py:process_turn` 和 `storage.py:create_memory / add_evidence`：

1. **引用有效性**：save 必须有非空、无重复、属于本轮 chunk 的 refs；Store 检查对应 Archive ID 存在。引用任意其他轮已存在的 ID 仍会被 chunk membership 拒绝，不是“只要数据库中存在就接受”。
2. **原文一致性**：正常 `Conversation.send` 使用同一 user / assistant 文本归档并构造 formation chunk；当前 LLM 只返回 candidate，不负责改写该 chunk。外部调用仍可提供真实 ID 配错误 role/content，程序没有重新读取原文比较。
3. **内容被证据支持**：Pydantic 验证结构，不验证自然语言蕴含。假设 user 原文是“我喜欢茶”，LLM 返回 save “用户喜欢咖啡”并引用该 user ID，若其他结构约束满足且没有 active 内容重复，**当前代码会接受**。这只是静态路径示例，不是本次真实模型结果。assistant-only evidence 支持用户事实也没有 Python 级语义 / 来源校验，限制主要写在 extraction prompt。

因此错误 evidence chain（准确 ID 指向不支持的文本）和错误长期 Memory 是真实可能性，不能只用 FK 或重读原文消除。但当前 prompt 已要求仅从对话提取、宁缺毋滥、区分 assistant 与 user、reject / uncertain 弱推断；A3 的有限真实 smoke 验证了部分正常案例，**没有测得错误率，不能断言高概率污染或零风险**。

错误 Memory 可以进入后续 recall / consolidation 并放大，所以不长期 DEFER。它不自动改写或删除 Archive，旧 superseded Memory 和 evidence 也保留，因此不是已证实不可恢复的原始数据损坏。保留记录只提供追查 / 重建依据，不代表当前已经有一键语义修复工具。在不要求 perfect semantic memory 的个人 Alpha 中归 FIX SOON，可带着观察，但不能把自动形成结果当成已验证事实。

### F03：双重表达不等于 active-only 失效

- 生产 extraction 调用 `create_memory` 时不传 `supersedes_memory_id`；production consolidation 调用 `supersede_memories`，事务更新旧 status 并写 `memory_supersessions`。
- supersession 历史 API `get_superseded_memories` 读取关系表；生产 retrieval 读取 `list_active_memories` 的 status 过滤，不读取单引用字段决定 active。
- 两种表达确实可以 divergence：正常 consolidation 后关系表有 link，而新记录单引用仍为 None；手动 `create_memory(..., supersedes_memory_id=...)` 则可有单引用、无关系 link，旧 status 仍 active。现有 `test_storage.py` 验证后一种行为。
- 这不证明当前私人数据库出现异常。本次没有读取 runtime 数据，不能回答其实际行状态。正常生产的关系表 / status 路径没有因此泄漏 superseded Memory；未来若用单引用重新推导 recall 状态才会出问题。

当前主要是历史兼容语义债务，不必 QQ Alpha 前统一 schema；Alpha 不应新增调用旧字段来执行 supersede。

### F04：当前可达，但不足以升级为强制 blocker

`MemoryFormationService.process_turn` 先保存全部 candidates，再按 saved 顺序 consolidation；`process_new_memory` 对其他候选只检查 active，不限制形成批次或先后。即使单进程、低频，某轮输出多个候选时也可出现后保存项被标为 old。

错误方向可能使新有效内容成为 superseded，从 active-only recall 消失，不能用“低并发”掩盖。另一方面 status 改变仍需要 judge 明确返回 supersede_old；保守 prompt 对不同时间范围、并列或可同时成立内容要求 keep_both。静态代码确认的是不受保证的方向，不是每次多候选都必然写错，当前没有高概率累积错误的实测证据。原文、旧内容与关系 link 保留，并非物理删除。

归 FIX SOON 而非长期 DEFER；优先做受控反例和最小 eligibility 决策。若 Alpha 实测反复出现最新版本被旧候选 supersede，应升级为 blocker 后再继续长期使用，不能把它当一般措辞问题。

### F05：正常启动与错误子集启动分开

`cli.py:main` 从固定 `data/worlds/si_world.yaml` 加载完整 seed；顺序为 Store schema 初始化 → World seed 插入 → legacy Life migration → Life service / Conversation。当前标准入口没有先传空 seed 或部分 seed 来做首次迁移。

World seed 插入与 Life 转换分别事务提交：插入成功、转换异常时，下次完整初始化可继续处理 marker=0 行；转换事务本身不会留下半行完成状态。转换成功后每行 marker=1，包括 unresolved 名称，不会在重启时自动再解析。这避免把开发者显式清空值填回，但意味着后来补 seed 也不会自动恢复旧引用。

原 legacy 文本未删除，可以未来人工确认后重新 resolve；当前没有自动 retry unresolved 的工具，不能说它会自行修复。子集初始化确有风险，现有 World 测试也使用补充 seed，但这些增量测试不等于标准入口先用子集迁移未处理旧库。当前固定完整启动路径下不阻塞 Alpha；若出现 unresolved，先检查诊断和原值，而非猜测新 UUID。

### F06：不是 Prompt duplication finding

原 P2 是数据文本进入 system 的信任边界，不是“Prompt 太长 / 重复”。重复认知边界尚无冲突或实测长度退化证据，不新增 required Prompt 重构。

Memory 来自 LLM extraction，仍可包含用户诱导的指令文本；“只是候选 / 不是系统事实”并不等价于明确禁止执行其指令。私聊并不保证完全没有不可信文本，但当前仅个人、无外部内容源、无自主工具或行动执行，且 Life / State 的自由文本来自显式开发操作，风险面有限。这里没有实时攻击测试或质量概率依据；归 FIX SOON，不以未经验证的 attack success 假设强行升 blocker。未来 adapter 也不能因为显示名称或聊天指令就授予内部 developer 写权限。

### QQ Alpha Minimum Safety Bar 与最终建议

这五个 P2 没有发现直接使用 development_id 错绑 Character、破坏原始 Archive、回退 State / Time 锚点的正常写路径。F01 已修复主回复 completion continuity；现有测试对 restart persistence 和失败路径提供有限代码证据，不是未来 OneBot adapter 的可靠性证明。Adapter disconnect / send failure、账号来源隔离、消息去重及逐轮调用仍需在独立 integration 任务中验证，本次不实现或另造 P2 finding。

统计：剩余 P2 **5**；BLOCKER **0**；FIX SOON **3**；DEFER **2**。

**Required before Alpha（仅针对本次剩余 P2）：无，最小 required patch 集合为空。** F02 / F04 / F06 为后续尽快处理列表，不混入 required list。以上判断没有批准读取私人数据库、迁移旧库或增加自动语义审判系统。

**Final Recommendation：QQ ALPHA READY。** 含义是：在上述限定运行假设和已修复 F01 的基础上，剩余 P2 不阻止进入受控个人 Alpha 实测；不是 QQ adapter 已实现、Memory 语义已证明可靠或可以无人值守运行的声明。应人工观察 formation / consolidation 诊断及异常记忆，并在触发范围扩大或发现反复污染后重新分类。

本次仅追加此 triage 节；执行 `ruff check .`、`ruff format --check .`、`git diff --check`，不运行 pytest、真实模型或 benchmark，不改生产代码、schema、测试及 fixture，不读写 runtime 数据或 secrets。
