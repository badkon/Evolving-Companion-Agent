# B7 — Action Intent + Action Resolver v0.1

## 1. Purpose

只接受受信任 developer、tests、internal code 显式提交的 `move_to`。仅 SI-001 是完整 Character。没有聊天解析、LLM 自动执行、自主决策、Scheduler、NPC 行动或远程动作入口。

通用能力 Direct Reuse 现有 Pydantic（MIT）、stdlib sqlite3 / SQLite（public domain）和 Clock。依据 [SQLite 事务文档](https://www.sqlite.org/lang_transaction.html) 使用 BEGIN IMMEDIATE 与短事务，依据 [Python sqlite3 文档](https://docs.python.org/3/library/sqlite3.html) 显式释放连接。这些成熟组件已在仓库使用，无新增依赖/外部代码；数据留在现有受保护 runtime DB，耦合限于小型 Store Protocol。业务校验和回执语义采用 Custom Implementation。ORM、工作流引擎、Action plugin 或 Event Sourcing 的依赖、耦合与迁移成本超出本阶段，未引入。

## 2. Intent vs Result

Intent ≠ Result ≠ World Fact ≠ Memory。请求不表示已完成；结果只记录当时动作的结论，不能替代当前 Life 的位置权威。动作成功也不产生亲历细节或 Memory evidence。

## 3. MoveToIntent

冻结、extra=forbid 的 Pydantic 模型：

| 字段 | 类型 / 语义 |
| --- | --- |
| action_id | UUID；调用方创建一次，重试保持原值 |
| character_id | UUID；必须匹配 Resolver 绑定的 identity.internal_id |
| action_type | Literal["move_to"]；默认 move_to，无其他动作 |
| destination_entity_id | UUID；目标 Place |
| expected_location_entity_id | 必填 UUID 或 None；None 明确表示提交依据的位置未知 |

不接受 SQL、Python、shell、路径或自由命令。非法外部数据通过 model_validate 拒绝；resolve 仅接受 typed MoveToIntent，不自动解析字符串/JSON。

## 4. ActionResult

冻结模型字段为 action_id、character_id、status、reason、destination_entity_id、location_before、location_after、resolved_at、replayed、outcome_known。resolved_at 来自注入 Clock，要求 aware datetime 并归一 UTC。

status 只允许 success / rejected / failed。reason 有限枚举：moved、already_at_destination、invalid_destination、location_precondition_failed、character_id_mismatch、life_context_missing、clock_moved_backwards、action_id_conflict、storage_error、commit_outcome_unknown。

failed 结果的位置字段未提供（None），不是断言当前位置未知或没有变化；outcome_known 描述本次事务是否能够确认，而不是完整 World 知识。

## 5. Character Location Authority

唯一当前位置仍是 `character_life_context.current_location_entity_id`，按固定 `identity.internal_id` UUID 绑定，不使用 SI-001 开发代号或 QQ sender ID。回执中的 location_after 是历史结果，不是第二份当前 location。NPC 位置继续由 B5 管理，B7 不修改它。

## 6. Validation

Resolver 构造绑定 Store、Character internal UUID、Clock，API 为 resolve(intent) 与 get_result(action_id)。先检查原回执及请求冲突，再校验配置 Character、已存在 Life、目标存在且为 Place、expected location 与最新 location 相等。无 Life 返回 life_context_missing，不初始化或猜测；目标无效返回 invalid_destination，不创建或按名称搜索地点。

前置条件通过后，已在目标位置返回 success / already_at_destination，不刷新 Life 时间。只有真正的位置变化才检查 Clock rollback。业务拒绝不改变位置，正常情况下保存 rejected 回执。

## 7. Transaction Boundary

同一个 connection、同一个 BEGIN IMMEDIATE 事务完成：查旧回执 → 读最新 Life → 校验目标/前置条件/Clock → 只 patch location + updated_at → 插入 terminal receipt → COMMIT。只有 COMMIT 成功才报告 success；写入必须影响一条记录，静默忽略的写入也视为失败并回滚。

禁止先 LifeService 更新提交、再另开事务写 receipt。事务内没有 LLM、HTTP、模型推理或 sleep。BEGIN IMMEDIATE 串行化当前 SQLite 写操作，不增加公共 UnitOfWork 或暴露 connection 到业务层。

## 8. Idempotency

相同 action_id + 完全相同请求字段返回原 terminal result，replayed=true，原 resolved_at 与位置结果不变。不会重新更新 Life、刷新时间或重写回执。不同请求复用 action_id 返回 rejected / action_id_conflict，不覆盖旧记录；该冲突结果不另存同主键回执。

重启仍读取数据库回执。action_id 是持久化幂等键，不是仅内存去重。回执查询失败不会用 None 假装没有记录；get_result 抛出安全的 action_result_lookup_failed，None 只代表记录不存在。

WF-R03：幂等范围是当前数据库保留的 receipt history，不是跨任意 backup restore 的永久去重。恢复到动作之前的完整备份会同时回退 Life 与回执；再次提交相同 action_id 时，若回执已不存在，按恢复后的 Life 重新 resolve，replayed=false。正式离线测试覆盖这个边界；没有数据库外的 receipt store。

## 9. Action Receipt

新增 `world_action_results` 表：action_id TEXT PRIMARY KEY、character_id、action_type、destination_entity_id、expected_location_entity_id（nullable）、status、reason、location_before/after（nullable）、resolved_at UTC ISO 8601。UUID 在 Python 层验证后存为文本。

SQLite CHECK 只允许 move_to、success/rejected 和可持久化业务 reason。failed 不持久化；replayed/outcome_known 是返回语义，terminal receipt 读回默认为 false/true。destination 无“当前实体必须存在”的 FK，允许 invalid_destination rejected 回执。无 pending/running/queued、job 或 WorldEvent 表。

## 10. A/B/A Replay Example

A：home → school 成功；B：school → home 成功；随后重放 A。返回 A 原来的 school 成功回执且 replayed=true，但 Life 仍为 home，updated_at 不变。回执不是当前位置。

## 11. Life Partial Patch Fix

CharacterLifeService 不再把更新前读取的整行写回。普通更新将显式字段验证为 LifeContextData，再由 Store 在同一 BEGIN IMMEDIATE 中读取最新行、校验引用、仅 patch 调用方指定字段与 updated_at。省略保持原值，显式 None 清空；no-op 不写、不刷新时间，UTC 与 rollback 边界保持。

首次初始化单独在写事务内检查缺失，只在仍缺失时插入；并发出现的 runtime 行优先，seed 不覆盖它。已有低层完整行 upsert 不用于 Service 的初始化/普通更新或 Resolver；运行路径使用 insert-if-missing / partial patch。

WF-R04：保留 `upsert_character_life_context` 作为低层完整替换 API，但禁止用 stale snapshot 做普通部分更新。运行路径回归测试禁止调用它；不是新的迁移或持久化框架。

确定性交错测试复现旧读 location=home → 另一连接 move 到 school → 原调用方只修改 role，验证 role 与 school 均保留。不依赖 sleep 猜竞态；双连接同 action_id 测试使用 Barrier。

## 12. Clock Rollback

需要实际移动且 resolved_at < Life.updated_at 时 rejected / clock_moved_backwards，位置和时间均不变。already_at_destination 不改状态，允许 no-op 回执，不无意义刷新时间。重放使用原时间，不制造新时间锚点。

## 13. Memory / State Boundary

不写 Memory、Evidence、embedding、supersession，不调用 extraction/formation/reranker，不修改 energy、attention、mood、social、current_activity。没有 CharacterEvent、WorldEvent 或 Experience。也不修改 NPC / Place。粗粒度位置变化不说明路线、耗时、交通、energy 消耗、活动或遇见人物。

## 14. Observation Integration

B6 不改：移动完成后，由调用方显式重新 observe，或下一轮 Conversation 自行捕获新快照。Resolver 不推送快照，不缓存环境，不接入 Conversation 的动作执行。自然语言“我去学校吧”和 assistant 回复均不会自动移动。/life 继续是 developer maintenance interface，不包装成角色自主动作。

## 15. Failure Semantics

存储异常确认 rollback 后返回 failed / storage_error / outcome_known=true，不保存假成功。busy/locked 同样不是 success；沿用既有 SQLite timeout，不添加自动 retry。

COMMIT 抛异常且事务仍 active、rollback 成功时可以确认回滚；COMMIT 抛异常但已经不在事务中，不能用空 rollback 证明未提交，返回 failed / commit_outcome_unknown / outcome_known=false。rollback 本身失败也保守报告 unknown，不宣称位置未变。调用方可显式查询原 action_id 或重提同请求；无重试框架。

结果在 COMMIT 前构造并验证，避免提交成功后才发现结果构造错误。异常文本、secret、完整 DB 内容不返回或打印。极端 close/storage 设备故障不由本版构建通用基础设施解决。

## 16. Resource Behavior

没有请求时无 Action 工作。每次显式 resolve 使用短 SQLite 连接，事务退出后关闭，无模型/API调用、后台 thread/task/timer、queue、常驻 connection 或 scheduler。测试使用临时数据库；并发线程仅为测试工具，不是生产工作方式。

## 17. Known Limitations

只有粗粒度 move_to；没有自主决策、LLM structured Action、聊天自动动作、route、movement duration、activity actions、NPC interaction、inventory、WorldEvent、Experience、Memory evidence、Scheduler、transport-level action API 或 Manager 页面。当前 trust boundary 是内部调用约定，不是新增的远程认证系统。

回执是 terminal outcome，不是完整行动历史或完整世界证据。没有生产规模/多设备吞吐验证、自动重试或通用 migration framework。离线 smoke 不是自主行为或真实模型验证。

验证：pytest、Ruff、pip check、diff check；`python scripts/run_world_action_smoke.py` 验证 A/B 观察、成功/拒绝、重启后 A/B/A 重放及临时目录删除；`python scripts/run_observation_smoke.py` 验证 B6 未回归。全部离线，不访问真实 runtime DB。

## 18. Future Autonomy Layer

未来自主意图或 structured decision 可在明确授权与设计后调用 Resolver，但本版不添加执行 hook、placeholder、Action registry、plugin、planning 或 B8。完成 B7 后停止。
