# B5 — Lightweight NPC Registry v0.1

## 1. Purpose

建立静态 World NPC 数据基础：谁存在、稳定身份、基础资料、当前地点关联和 active 标记。只实现 Tier 0，正式 NPC 初始为 0；没有人物已明确设计，因此不新增 NPC seed。测试和 smoke 人物仅存在于临时数据库，不是正式世界设定。

## 2. Why NPC is Lightweight

默认成本接近 SQLite 中的记录，而不是运行中的 Agent。通用能力 Direct Reuse 现有 Pydantic、stdlib sqlite3 和 Clock，项目特定的 World 边界为 Custom Implementation。依据 [Python sqlite3 文档](https://docs.python.org/3/library/sqlite3.html) 和 [SQLite FK 文档](https://www.sqlite.org/foreignkeys.html)，沿用成熟的短连接、参数化 SQL、事务和外键检查。

SQLite 为 public domain，Pydantic 为 MIT；没有复制外部代码或增加依赖。现有库已在项目中使用，运行不需要网络；World 数据独立于认知链，后续存储替换通过小型 NPCStore Protocol 完成。ORM、ECS、Agent/社会模拟框架的功能、依赖及耦合超出当前静态需求，未引入。

## 3. NPC vs Character

Full Character：仅 SI-001 / 玲。NPC 是轻量 World data，不是 Character Core、完整 Agent 或具有独立认知系统的个体实现。NPC UUID 与 Character internal UUID、开发代号及 World Place UUID 语义分开。

```text
World
├── Places (world_entities)
└── Lightweight NPC Registry (world_npcs)
        └── place reference → Places

SI-001 Character Core
        X 不直接读取全部 NPC

Future at B5 delivery (now separately implemented by B6/B7):
World → Observation Layer → SI-001
SI-001 intention → Action Resolver → World
```

## 4. Schema

NPCRecord 为冻结、拒绝额外字段的 Pydantic 模型：

| 字段 | 语义 / 存储 |
| --- | --- |
| npc_id | UUID，create 时 uuid4 一次生成；持久化为 TEXT 主键 |
| canonical_name | 非空名称，不作为主键，不要求唯一 |
| display_name | 可空展示名称 |
| place_entity_id | 可空 Place UUID FK |
| active | bool，SQLite INTEGER CHECK 0/1 |
| tags | 不可变 tuple[str, ...]，持久化为 JSON 数组；只作标签 |
| short_description | 可空简短资料，不是人格或亲历记忆 |
| created_at / updated_at | aware datetime，统一转 UTC，存储为 ISO 8601 |

文本不接受全空白；nullable 文本用 None 清空。未添加年龄、情绪、活动、目标、技能、关系评分或日程字段。tags 使用 tuple 避免冻结记录内部列表仍可变，不建立标签本体或 tier 字段。

## 5. Persistence

独立 `world_npcs` 表不改变 B4 的 place-only `world_entities`。SQLiteStore 提供 get_npc、list_active_npcs、insert_npc、patch_npc；NPCService 提供 get、list_active、create、update、set_location、set_active。不增设 repository hierarchy、history 或 event 表。

Closure 修复 WF-R01：普通更新由 Store 在单个 `BEGIN IMMEDIATE` 短事务内读取最新 NPC、验证合并结果、仅写显式字段与实际变化时的 updated_at，再提交。省略字段保留最新数据库值，不把过期整行写回；UUID/created_at 不可 patch。无变化不写；校验、Place 引用或写入失败回滚。事务内不调用模型或后台工作。既有低层 update_npc 完整写入方法保留，但不用于 NPCService 普通更新。

只由显式调用创建和修改；服务构造、读取、重启和 Clock 推进不初始化 NPC 或覆盖 runtime。update 省略字段不变，None 清空可空值，no-op 不写入或刷新 updated_at；真实变化更新时间，created_at 和 UUID 不变。时钟倒退的变化请求明确拒绝并保留原记录。active=false 不物理删除，get 仍可读并可显式重新激活。更新不存在 ID 报错，不隐式创建。

每次操作使用并关闭独立连接、启用 foreign_keys；继承既有 DB 路径配置，不访问新的数据库文件。Runtime 数据继续被 Git 忽略。PK 索引和 active/place 索引支持常规访问，不做更复杂优化或多设备并发同步。

## 6. Location

place_entity_id 表示 World 当前记录的 NPC 位置，可为 None；不是移动路线或活动。非空值必须已存在且 entity_type=place：FK 与 INSERT/UPDATE 地点检查 trigger 在 SQLite 层拒绝缺失/非地点引用，包括直接 SQL 写入。不能自动创建 Place，也不根据名称猜测地点。B4 仍只允许 place，不把 NPC 塞进 WorldEntity。

## 7. World Truth Boundary

World Truth ≠ Character Knowledge ≠ Character Observation ≠ Character Memory。NPC 数据存在、地点被更新，不说明玲见过 NPC、知道其位置、与其交往或记得任何经历。更新不触发 State、Life Context、Archive 或 Memory 写入。

## 8. Resource Model

无人访问时没有 NPC 工作，NPC 相关 CPU ≈ 0、API/LLM/embedding/reranker calls = 0。服务只持有 store 和 Clock，get/list 为直接 SQLite 查询；没有常驻连接、每 NPC task、thread、coroutine、timer 或后台扫描。设计适用于数百条静态/低频记录；未进行规模性能 benchmark，不作实测吞吐承诺。

## 9. No LLM

没有 NPC Client、system prompt、对话历史或模型调用。B5 交付时 Conversation、CLI、QQ、Manager 和 Setup 均未接入 NPC；之后 B6 已通过只读 Observation 向 Conversation 投影有限匿名存在信息，并非 NPC 对话或认知系统。

## 10. No Memory

没有 NPC memory/evidence/embedding/supersession 表，不复制 A3 Memory System。创建、位置变化和停用不形成记忆、知识或观察。

## 11. No Scheduler

没有 world tick、日程、课程表、睡眠循环、活动或自动移动。Clock 只用于显式写入时间，不驱动 NPC 生活。

## 12. Future Observation Layer

“Observation 尚未实现”是 B5 交付时的历史状态；当前 [B6 Observation](b6_observation_layer_v01.md) 已提供同 Place + active 的有限匿名线索。B5 Registry 本身不枚举并注入全部 active NPC，不向 Prompt 投影名称、位置或描述，也不赋予全知视角。

## 13. Future Action Resolver

当前 set_location 是开发者/系统显式数据接口，不是角色行动。未来意图 → Action Resolver → World Mutation 的边界需单独设计；Character / NPC 不应直接操纵 World DB。本版不实现 Action、Resolver 或自动调用。

上述为 B5 历史范围；当前 [B7 Resolver](b7_action_resolver_v01.md) 已另行实现 SI-001 的受信任显式 move_to，不自动驱动 NPC。

## 14. Future Important NPC Tier

只记录未来概念：Tier 0 static NPC、Tier 1 lightweight simulated NPC、Tier 2 important limited-cognition NPC。当前只有 Tier 0；没有通用 tier framework、重要 NPC 认知或有限记忆。Full Character 当前仍为 SI-001 only。

## 15. Known Limitations

B5 Registry 本身无正式人物 seed、观察、对话、自主行为、社会网络、关系演化、角色生成、背景工作或调试 UI；当前有限观察由 B6 承担。Registry 是当前快照，不保留位置历史或完整 World event。标签与描述不自动成为 Character Context；未来读取权限、重要 NPC 及世界模拟不由本阶段扩展。

验证入口：`python scripts/run_npc_registry_smoke.py`，仅临时 SQLite + FixedClock + 现有地点 seed；创建 synthetic NPC、到校、移到住宅区、停用、重开 Store、确认持久化与临时目录删除。pytest 离线覆盖 UUID、校验、位置、UTC/no-op/重启及 Prompt/State/Life/Memory/Place 和资源边界，不下载模型或调用 API。
