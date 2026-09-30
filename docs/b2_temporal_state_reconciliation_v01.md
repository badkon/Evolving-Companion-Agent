# B2 — Temporal State Reconciliation v0.1

## 1. Passive State Reconciliation

B2 复用 `CharacterStateTransitionService.apply_elapsed_time()`，根据当前 UTC 时间与 State 的持久化时间锚点计算确定性归一。没有新增 reconciliation 服务层，也不生成或保存假 Event。

本次采用 Adapted Reuse：扩展现有 A4.1 transition；校验与存储继续复用现有 Pydantic、stdlib datetime 和 SQLite。没有引入外部框架或代码，依赖、许可证与本地运行数据边界保持现有方式。

## 2. Baseline semantics

工程中性 baseline 为 `energy=medium`、`attention=normal`、`mood_tendency=neutral`、`social_engagement=normal`。这些默认值与阈值是工程 heuristic，不是人格或心理学结论。

## 3. Explicit Event vs Time Passage

显式 Event 可以形成短期偏离，例如 `rest_started` 仍使精力上升一级，`mood_up`、`social_engagement_down` 仍使用现有规则。随后时间经过可将这些偏离归一。相同值的 no-op Event 不刷新锚点；真正变化的字段才记录变化时间。

Time Elapsed ≠ Lived Experience。归一不代表玲睡过觉、休息过、吃过饭、上过课或发生过其他经历。

## 4. Offline Duration vs State Elapsed

`offline_duration` 来自最后一次成功完成并归档的对话时间，只用于 B1 时间上下文。`state.updated_at` 是整行最后一次实际状态变化时间；每个参与归一的字段另有自己的变化锚点。Reconciliation 不读取 `last_interaction_at`。

例如上次交流 T0，energy 在 T0+2h 被显式设为 low；T0+5h 的 offline 是 5h，而 energy elapsed 是 3h。其他字段的变化不会重置 energy 的锚点。

## 5. Field-level time anchors decision

单一 `updated_at` 无法表达独立阈值：T0+3h 的 energy 变化会错误推迟原本 T0+6h 的 mood 归一。因此 `CharacterState` 增加四个 UTC aware 锚点：`energy_updated_at`、`attention_updated_at`、`mood_updated_at`、`social_updated_at`。

显式更新与被动归一只更新实际变化字段的锚点，同时更新整行 `updated_at`。其余字段保留原锚点；无变化检查不写回数据库。Activity 没有衰减需求，不增加 activity 时间锚点。

SQLite 初次打开旧 `character_state` 表时，在一个事务内添加缺少的列，并用原 `updated_at` 回填。旧字段、UUID 和记录均保留，重复初始化不覆盖已建立的字段锚点。新表锚点列为 NOT NULL；旧表通过最小 ALTER/backfill 兼容，不创建历史表或 migration framework。旧版本没有逐字段历史，回填仅保留已知的原始锚点。

## 6. Energy rules

- low 持续至少 3h → medium。
- medium 保持 medium。
- high 持续至少 8h → medium。

取消 A4.1 的纯 elapsed 自动升至 high；显式 `rest_started` 仍可 medium → high。

## 7. Attention rules

focused / scattered 持续至少 1h → normal。normal 不因时间经过变成偏离状态。

## 8. Mood rules

positive / low 持续至少 6h → neutral。neutral 不因时间经过产生其他心境。

## 9. Social rules

engaged / withdrawn 持续至少 6h → normal。normal 不根据 offline 自动变得疏离或投入。

## 10. Activity preservation

`current_activity` 不因 elapsed 清除或生成。只有显式更新或已有 Event 映射改变它；现有 focused_task_ended 仍只恢复 attention，不清除活动。长时间保留的活动可能已不符合现实，但系统没有真实结束信息时不会猜测。

## 11. Clock rollback

当 now < state.updated_at 时，返回 `clock_moved_backwards`，所有状态与时间锚点保持不变。恢复后继续从保留的字段锚点计算，不产生负时间变化。Conversation 可继续生成回复。

## 12. Known Limitations

归一由 Conversation 或显式调用触发，没有后台推进。变化时的锚点记录 reconciliation 实际执行时间，不虚构阈值达到时发生的 Event。保留活动可能过期；阈值暂为固定工程 heuristic。

B2 没有 offline life、sleep、routine、activity/world simulation、LLM State inference、随机漂移、关系/人格演化、scheduler 或 Event history。

## 13. Future Work

更细的活动结束、作息或世界时间需要后续独立需求与设计。当前只建立 temporary State 的时间连续性。

## Conversation and verification

Archive user → Clock snapshot → temporal reconciliation → Time Snapshot → recall → Prompt → LLM；State 与时间投影共用同一个 now。Prompt 继续只投影语义状态，四个时间锚点不进入模型上下文。

离线验证：`python scripts/run_temporal_state_reconciliation_smoke.py`。测试使用临时 SQLite 和 FixedClock，覆盖阈值、累计检查、独立字段、旧数据库回填、回拨与 Conversation 注入。
