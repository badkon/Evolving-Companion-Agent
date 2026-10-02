# B6 — Observation Layer v0.1

## 1. Purpose

提供有限、只读、瞬时的当前环境线索。只有 SI-001 是完整 Character；正式 NPC seed 仍为 0，测试人物仅在临时数据库存在。本阶段不是生活模拟、感官模拟或自主 Agent。

直接复用现有 Pydantic（MIT）、stdlib sqlite3 / SQLite（public domain）与 Clock；没有新增依赖或复制外部代码。它们已用于本项目，维护成熟、无观察网络成本，耦合限于小型 Store Protocol，后续替换不需迁移观察历史。依据 [SQLite 事务文档](https://www.sqlite.org/lang_transaction.html) 使用显式读事务维持快照，依据 [Python sqlite3 文档](https://docs.python.org/3/library/sqlite3.html) 显式关闭连接。过滤与匿名投影采用项目特定的 Custom Implementation；完整 ECS、感官/Agent 框架的依赖和反向耦合超出当前需求，未引入。

## 2. Observation vs World Truth

World DB 是当前系统事实，不等于角色全知视角。Observation 仅从当前 Place 选择有依据的少量线索，不注入全城 NPC 或全部 World 描述。

## 3. Observation vs Knowledge

Observation ≠ Knowledge。内部 NPC UUID 只作记录引用，不证明玲认识人物；名称、标签、资料不成为角色知识。没有 Knowledge Store 或人物识别机制。

## 4. Observation vs Experience

Observation ≠ Experience。快照不是持久经历，不记录访问历史、行动过程或离线活动。没有 Experience Store。

## 5. Observation vs Memory

Observation ≠ Memory。观察不调用 extraction、formation、create_memory、supersession、embedding 或 reranker；不伪造 Archive，不新增 evidence kind，不改变 A3 source="observed" 的既有语义。A3 原算法与正常对话流程保持。

## 6. Character Location Source

Character key 始终是 Seed 中固定的 `identity.internal_id` UUID，不是 development_id 或 QQ sender ID。唯一当前位置来自 `character_life_context.current_location_entity_id`。学校/家关联、时间和聊天内容不能推断位置。

`ObservationService.observe(character_id, now_utc=None)` 返回不可变 `ObservationSnapshot`；`capture_context(...)` 同时返回 `(ProjectedLifeContext | None, ObservationSnapshot)`。Snapshot 字段为 character_id、UTC aware observed_at、可空 location_entity_id/place_name、不可变 visible_npc_ids、truncated、status（available / unknown_location）。没有 observation UUID、history 或 cache。

缺失 Life 行或当前位置为 None 时返回 unknown_location，不查询 NPC、不初始化 Life、不猜测地点。既有 CLI / QQ composition 在初始化 World 后显式初始化缺失 Life；这是入口原有初始化职责，不在 Observation 中执行。

## 7. NPC Visibility Rule

NPC 位置唯一来自 `world_npcs.place_entity_id`。同一个只读 SQLite 事务内读取 Life、相关 Place 名称与以下限定查询：

```sql
SELECT npc_id FROM world_npcs
WHERE place_entity_id = ? AND active = 1
ORDER BY npc_id LIMIT 21;
```

最多返回前 20 个 UUID；第 21 条仅判断 truncated。20 是查询/上下文边界，不是角色的生理感知上限。NPC 位置为空、inactive、其他地点、parent/child 或同区域人物均不纳入；不使用 list_active 全城扫描。复用 B5 已有 place/active 索引，无新增索引或 schema 变化。

“同 Place”只是粗粒度近似，不是真实视线、距离或遮挡。尤其不能理解为看见学校里所有人。

## 8. Anonymous NPC Projection

Prompt 仅获得本次匿名人物存在信息，不获得 canonical_name、display_name、description、tags、UUID、时间戳或数据库 metadata。空结果表述为“当前观察中没有额外的人物信息”，不宣称周围无人。truncated 明确表示仅有部分信息，不声称完整枚举或人数上限。

## 9. Snapshot Lifecycle

每轮重新 capture；快照只在 send 的局部变量中存在，不进入 history，不存储，也不复用上一轮。位置显式变化后下一轮读取新地点。与 B1 时间快照、B2 State reconciliation 复用同一轮 now_utc。当前读事务结束、连接关闭之后才调用 LLM；不在模型调用期间持锁。

## 10. Prompt Integration

PromptBuilder 的 observation 参数可选，生成独立 `【当前可观察环境】` 区块，不混入长期记忆区块。开启观察时，组合 Life 与 Observation 的地点/生活文本采用 JSON 字符串引用和转义，明确仅是数据、不是系统指令；World/NPC 文本不得改变规则。仅投影允许字段，不投影 Place description。

现有 Character 身份、人格、关系、认知、对话行为与三条中性示例不变。未启用观察时原 Conversation / Life Prompt 路径保持原样。CLI / QQ composition 共用 Store、Clock 和 internal UUID；没有新增命令或 Setup / Manager 页面。

## 11. Failure Boundary

组合查询失败时丢弃本轮 Life/Observation 两块，避免不同版本混合；不使用旧快照、不伪造空世界、不把异常内容放入 Prompt。`last_observation_error` 与 warning 仅保留异常类型，每轮清空。原主链可用时继续生成回复。

user Archive、主 LLM、assistant Archive 失败仍遵循原有完成边界。last_interaction / Memory Formation 的既有 best-effort 行为不改变。观察失败不表示主回复一定可用，例如 Archive 本身不可用仍会失败。

## 12. Resource Behavior

无请求时没有观察工作。按需执行本 Character Life 与至多四个关联 Place 读取，再执行至多 21 条 NPC 引用查询；短连接、参数化 SQL，PRAGMA query_only 防止该连接写入。事务成功/失败均退出并关闭连接。没有 LLM/embedding/reranker 调用、thread、timer、后台 task、扫描或 world tick；未做规模吞吐 benchmark，不承诺实测性能。

## 13. Known Limitations

只有静态 Registry 的有限同地点线索；无识别、视线、房间级空间、距离、遮挡、关系、动态地点、World Event 或完整城市人口。快照完成后世界仍可能变化，不对后续生成期间的实时性作保证。

**Memory evidence 限制：** 玲可能在 assistant response 提及观察信息，既有 A3 Formation 随后可能处理这段 assistant Archive。它仍只是 Conversation evidence，不等于原始 World Fact 已获得完整 Memory evidence 支持。B6 不宣称解决此问题，也没有添加自动 World Memory 流程。

离线 pytest 使用临时 SQLite、FixedClock、fake LLM，覆盖位置过滤、匿名/转义、截断、逐轮刷新、一致读事务、故障降级、无写入与资源边界。`python scripts/run_observation_smoke.py` 使用 synthetic NPC 检查地点 A/B 切换及匿名 Prompt，退出后删除临时 DB/目录。没有访问真实 runtime DB、QQ、API 或模型下载；不是自然语言生成质量的真实模型验证。

## 14. Future Experience Layer

未来合理路径为 World Fact → Character 实际感知 → Experience → 重要性判断 → Memory Candidate → Memory + Evidence。这里只记录方向，不创建表、service、占位模块或 integration hook。

## 15. Future Action Resolver

未来意图 → Action Resolver → World mutation 须另行设计。B6 不实现 Action、move_to、Scheduler、NPC 日程、自主移动或事件；不修改 State、Life、NPC、Place 或 Memory。显式测试位置更新只是已有 LifeService 开发接口，不是角色行动。
