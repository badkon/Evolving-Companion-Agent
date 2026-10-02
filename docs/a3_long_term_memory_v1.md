# A3 Long-term Memory v1

状态：**Long-term Memory v1 accepted baseline**。A3 阶段已结束；下文冻结的是当前可运行基线，不代表所有未来方案永久禁止，也不代表记忆能力已达到人类水平。

## 1. Goals

A3 为 SI-001 建立最小可用的长期记忆闭环：保存原始对话与有证据的 Memory，按需召回并谨慎提供给对话模型，在成功对话后形成新 Memory，并对明确过时的旧状态执行可追溯的 supersede。

边界保持：**Archive ≠ Memory ≠ Character Knowledge ≠ Lived Memory**。Archive 是系统保存的原始记录；Memory 是经 Gate 接受的结构化记录；保存或召回一条记录都不自动证明 Character 亲身经历过它。

## 2. Final Architecture

权威记录保存在 SQLite：`archive_messages`、`memories`、`memory_evidence` 和 `memory_supersessions`。`memory_embeddings` 是依据 Memory 与模型名缓存的派生索引，可重建；它不是 Memory 内容的权威来源。默认运行库为 `runtime/si_001.db`，Archive 与 Memory 分离保存。

生产链路由 `Conversation` 协调：`MemoryNeedPolicy` 和 `MemoryContextPolicy` 决定是否检索及是否附带近期对话；`MemoryRetriever` 使用 `BAAI/bge-base-zh-v1.5` 在 active memories 中做 semantic Top-10；`MemoryReranker` 使用 CPU `BAAI/bge-reranker-base` 重排；最多 Top-3 作为可忽略的候选上下文传入 Prompt。随后成功回复被归档，`MemoryExtractor` 对当前 user/assistant 消息对执行结构化提取与 Gate，合格候选持久化并进入 `MemoryConsolidationService`。

## 3. Components

- `storage.py`：标准库 `sqlite3`、Archive、Memory、evidence、supersession link 与 embedding cache 的最小存储 API；关键枚举同时由代码校验和 SQLite `CHECK` 约束。
- `memory_extraction.py`：单次结构化提取，候选决策为 `save` / `reject` / `uncertain`；验证本轮 Archive evidence，执行 normalized active-content dedup，只保存有效 `save` 候选。
- `embeddings.py`、`memory_retrieval.py`：CPU BGE embedding、active memory cosine retrieval 与缓存。
- `memory_policy.py`、`memory_recall.py`：基于显式线索的 Memory Need 与 Selective Context；需要时构造 current-only 或带最多 5 条近期消息的检索 query，并协调 Top-10 到 Top-3 流程。
- `memory_reranker.py`：CrossEncoder 候选排序；不把归一化分数解释为通用相关性门槛。
- `memory_consolidation.py`：新 Memory 保存后召回最多 5 条相关 active 旧 Memory，逐对判断 `keep_both`、`supersede_old` 或 `uncertain`，仅在全部判断完成后提交 supersession 状态与关系。

Embedding 和 CrossEncoder 模型按需加载，生产实现使用 CPU。实验 runner 与生产检索链路分开；fixture 和实验工具不等于生产能力。

## 4. Accepted Decisions

以下为 **Long-term Memory v1 accepted baseline**：

1. 原始对话先进入 Archive；Memory 必须另行形成，并绑定真实 Archive message evidence。
2. 仅 active Memory 参与生产 retrieval；superseded 记录保留，不覆盖其内容或 evidence。
3. Retrieval 是按需的：Memory Need 为 false 时不检索、不注入；为 true 时仅在规则识别到回指时拼接近期上下文。
4. semantic Top-10 经 CrossEncoder 排序后，最多注入 Top-3 候选；Prompt 明确这些记录不是系统事实，可以忽略。
5. 形成只发生在成功回复并保存 assistant Archive 后，因此同轮新 Memory 不参与该轮 recall。`reject`、`uncertain`、证据无效或 extraction 失败不形成 Memory；formation 是 best-effort，不回滚已成功回复。
6. 新 Memory 与旧 Memory 的冲突处理保守：只有明确状态替代才 supersede；旧记录及其证据保留。Consolidation 失败不撤销新 Memory，也不影响主回复。
7. 当前没有自动 Memory summary、merge、forgetting 或长期历史压缩。

## 5. Rejected Experiments

下列方案是 **A3 v1 当前拒绝 / 延后方案**，不是永久禁令；未来若有新证据，应在明确新阶段中重新评估：

- 不用 salience bonus 排序；已有 salience/recency 实验观察到排序退化。
- 不用 recency bonus 排序；当前排序不按创建时间加权。
- 不把单一 reranker threshold 当作完整 Memory Gate；相关性分数不能替代“当前问题是否需要个人记忆”的判断。
- 不 always use recent_context；近期上下文虽能救回部分指代，但普遍拼接会造成 context pollution。
- 不 always retrieve memory；普通问题没有个人记忆需求时跳过 retrieval。
- 不把所有记忆注入 Prompt；注入仅限少量排序后的候选。
- 不把 Memory 当作系统事实，也不把每条 Memory 自动当作 Character 的 lived memory。

## 6. Production Flow

```text
User message
  ↓
Archive user message
  ↓
Memory Need ── false → 不检索、不注入
  ↓ true
Selective Context
  ↓
Retrieve active memories
  ↓
Semantic Top-10
  ↓
CrossEncoder rerank
  ↓
Top-3 candidate injection
  ↓
Generate assistant response
  ↓
Archive assistant response
  ↓
Memory Extraction + save gate + evidence validation + dedup
  ↓
Persist approved memories
  ↓
Conflict / Supersede check
```

Archive user message 先于 recall 与模型调用写入；模型失败时 user Archive 仍保留。Assistant Archive 与 in-memory conversation history 仅在回复成功后追加。Memory formation 随后运行，不负责从数据库恢复当前对话历史。

## 7. Failure Semantics

- 主 LLM 调用失败：user Archive 保留；assistant Archive、history 更新及 Memory formation 不发生。
- Recall/retrieval/reranker 失败：A3 冻结时异常会中断本轮回复；后续 A6.3 已将 Conversation recall 改为本轮 no-memory context 的 best-effort 边界，继续主 LLM，且不跳过成功回复后的 formation。当前策略见 [A6.2/A6.3 Availability Policy](a6_2_api_deployment_profile.md#7-failure-semantics)，不改写本节历史 smoke evidence。
- Extraction、验证或保存阶段失败：主回复与已写 Archive 保留；formation 异常被记录为诊断类型，不向用户暴露 provider 错误细节。
- Consolidation/retrieval/judge/状态更新失败：新 Memory 不回滚；旧状态不会在全部 judge 决策完成前部分更新；失败作为 consolidation diagnostics 返回。

## 8. Smoke / Benchmark Evidence

本节记录 A3 阶段已有实验/烟测结论，不在 Closure 中重跑 benchmark。合成 fixture、fake dependency 单测或有限 smoke 只验证给定案例与流程，不能证明自然语言泛化。

- Semantic retrieval benchmark 的阶段观察为基础 recall 表现良好；salience/recency bonus 实验出现排序退化，因此未进入生产排序。
- CrossEncoder 在已测候选集上能改善候选顺序；relevance gate benchmark 显示单一 reranker threshold 不能代表 Memory Need。具体分数不作为普遍性能保证。
- Recent context 在已测 pronoun、ellipsis 与 back-reference 案例中帮助找回指代对象；always-context 案例出现 context pollution；rule-based Memory Need + Selective Context 在该 fixture 上降低了这类污染。
- A3.3 real smoke 的阶段记录包括 personal recall、generic knowledge、context rescue 和 greeting 案例符合预期；它们是案例级行为检查，不是覆盖率或泛化证明。
- A3.4 real smoke 的阶段记录包括 birthday formation 后 next-turn recall、generic PINN query 与 greeting 不形成 personal memory，以及 Mac plan 可形成 Memory；这些结果仍受有限 smoke 案例范围限制。
- **A3.5 real smoke verified：**真实 extraction 将“我现在不打算买 Mac 了”保存为新 cancellation Memory，真实 consolidation judge 判为 `supersede_old`；旧 Mac plan 变为 `superseded`，新 cancellation 保持 `active`。后续查询只 recall 到新的 active cancellation，旧计划未进入 production recall。
- **A3.5 real smoke verified：**“用户喜欢策略游戏”和“用户喜欢剧情游戏”均成功保存；真实 judge 判为 `keep_both`，两条 Memory 均保持 `active`。
- **A3.5 real smoke verified：**已有“用户喜欢咖啡”保持 `active`；针对“我今天不想喝咖啡”，真实 extraction 未生成长期 Memory candidate，因此未触发 consolidation，也没有错误 supersede。
- 以上只是少量真实 smoke case，验证给定输入上的端到端行为；不代表 consolidation judge 对所有自然语言冲突都可靠，也不证明 Memory Extraction 可泛化到未测试表述。确定性单测覆盖的代码路径是补充证据，不替代真实模型评估。

## 9. Known Limitations

1. Memory Need Policy 是基于字符串线索的 rule-based policy；当前 fixture 表现不能保证覆盖所有自然语言。
2. Context Policy 同样是 rule-based；明显回指有效，更复杂、跨轮或隐式语义回指仍可能失败。
3. Top-3 候选可能包含不相关 Memory；目前依赖 Prompt 与 LLM 忽略不相关项，没有 production candidate filtering threshold。
4. 每轮成功回复后都会尝试一次 extraction LLM 调用；成本未优化，没有 batching 或 background worker。
5. 每条新 Memory 可能再触发最多 5 个 pairwise consolidation judge LLM 调用；复杂冲突仍可能漏判或误判。
6. 当前只有 normalized active-content dedup，没有 semantic dedup。
7. 没有 merge、summary consolidation、forgetting/decay 或 periodic reflection。
8. 没有 memory confidence model；`salience` 不能替代置信度。
9. Recent conversation history 仍可能包含已 supersede 的旧事实；即使长期 recall 只返回 active Memory，短期历史仍可能使模型提到旧状态。
10. 时间语义尚不成熟；“刚说过”“以前”“最近”等关系主要交由当前语言模型依据上下文表达，没有独立的 temporal grounding。
11. Recall 之前的检索/重排错误会中断本轮对话；当前没有与 formation 相同的 best-effort 隔离。
12. 现有人工 smoke 与 benchmark 输出没有作为可复现的正式评测报告/数值基线完整保存在仓库中；本节只保留定性结论与验证边界。

## 10. Deferred Work

以下工作不属于已实现能力，也不在 A3 Closure 中启动：

- candidate filtering v0.2
- semantic dedup
- conflict consolidation summary
- memory merge
- forgetting / decay
- reflection
- temporal grounding
- conversation history compression
- async extraction
- cheap extraction model routing
- future Developer Console 的 memory inspection

## 11. Exit Criteria

A3 被认为完成，因为当前系统已经可以：

- 保存长期信息并为 Memory 绑定 evidence；
- 在后续对话中按需召回，并避免多数无关问题强行召回；
- 使用近期上下文解决部分回指；
- 从成功对话尝试形成新 Memory，且不让同轮新 Memory 自我召回；
- 对明确状态更新执行 supersede，同时保留旧历史，只让 active Memory 参与生产 recall。

A3 不要求人类级 autobiographical memory、完整时间推理、全自动反思、无误差冲突解析、无限上下文或 memory graph。阶段完成表示 v1 基线及其边界已经明确，不表示上述限制已解决。
