# Affective & Relationship State v1

## 实际链路

```text
已授权文本 → user Archive → Event
→ Planner + Appraisal（同一次 LLM）
→ Emotion → Mood → Interaction gate → Relationship（单事务）
→ 新状态投影 → Expression Selector / Replyer（第二次 LLM）
→ assistant Archive / history → 原有 Memory Formation
```

Emotion、Mood、Relationship 是不同状态，不是好感度。Character key 使用
`identity.internal_id` UUID。不修改 Character Seed / Constitution / World；没有
第三个情绪 LLM、后台 scheduler、Vision、主动消息、恋爱系统或新 Memory schema。
正式 CLI / 共享 Core 工厂接入；legacy 不进行 Appraisal，none 启动不调用模型。

## Event / Appraisal

Event 包含稳定 id、type、actor、target、summary、UTC timestamp、source、significance。
聊天 id 来自 user Archive UUID。其他 type 为 image/sticker observation、world/time
event、character/user action 提供受信输入接缝；没有对应生产者或 event bus。

Planner 增加 event_significance、appraisal（relevance/valence/novelty/social_meaning/
reality）、最多三个 emotion_impulses、relationship_signal、cause_summary、worth_remembering。
Pydantic 校验范围、词表及额外字段；LLM 不能写最终 trust 或 delta。
失败时不更新状态，仅日志记录异常类型，继续保守回复。

## Emotion

支持 joy/sadness/anger/annoyance/fear/surprise/curiosity/affection/relief/disappointment。
保存 type/intensity/target/cause/created_at/decay_until/source_event_id。
程序将 impulse 乘 relevance，最大 .8，低于 .05 不保存；寿命 20–120 分钟，
读取时线性衰减。过期不投影、更新时清理，活跃集合最多 24 条；原消息仍在 Archive。
时间倒退不放大情绪。感受属于玲，其他人的私人原因不投影给当前对话者。

## Mood

四维 [-1,1]；baseline 固定 valence=.15、energy=.05、calmness=.25、sociability=.10。
新事件只积分一次，每维每事件最多变化 .04。读取/更新时按六小时半衰期回归 baseline，
重启按时间差继续计算，不随机初始化、不需要后台任务，rollback 不后移时间锚点。

A4 任务精力、注意力、活动保留。启用新系统时，不再同时投影原 A4 mood/social
为另一套心境；任务精力与主观 Mood energy 明确区分，旧数据不删除。

## Relationship / Interaction gate

五维 [0,1]：familiarity/trust/closeness/comfort/formality；stage 为 stranger/acquainted/
familiar/close；romantic 严格为 false。

按本轮明确要求，主要用户首次初始化 familiar 与 .75/.65/.65/.80/.15。
这是显式关系 bootstrap，不从开发者身份推导信任，不伪造共同经历；早期 Design 的
“特殊初识”保留为历史起点，不改写成过去一直熟悉。

QQ 标准化 sender 转为稳定 UUIDv5 relation target，不硬编码真实账号，不将账号或
target UUID 交给 LLM。假名键不是加密或不可逆匿名承诺。首次 QQ 配置使用有序 allowlist
第一项作为主要用户并持久化绑定；之后重排不提升其他人的关系。none/local 占位目标
在首次配置 QQ 时允许绑定一次。其他目标独立初始化 stranger，重启不覆盖关系。

仅 meaningful、significance>=.6、relevance>=.5 且合法的关系事件通过 gate：
重要私人披露、持续可靠支持、有依据失约、严重冲突。问候/夸奖/成果/抱怨/临时改计划
不改变关系；计划/假设/共享现实物理声称不是关系证据。事件类型另限制可更新维度，
例如披露不能自动提升 trust。

普通 step 为 .002/.006/.01；高重要性严重负面事件最多 -.02。
每维 UTC 每日**绝对累计**上限 .03，正负交替不退预算，重启不重置，时钟倒退不重置日。
comfort 带来受同样预算约束的 formality 半量变化。stage 用确定性阈值；无 romance stage。
当前轮用更新前关系，下一轮用新关系；Emotion/Mood 当前轮立即生效。

## 持久化和完成边界

同一 SQLite 文件新增独立表：affective_state、relationship_states、emotion_events、
interaction_events、affective_receipts、relationship_budgets、affective_primary_target。
v1 使用严格 Pydantic payload JSON，SQL CHECK 保证 JSON 有效，FK 约束 Character/关系关联。
BEGIN IMMEDIATE 将 receipt、情绪、Mood、关系与预算一起提交或回滚；连接每次显式关闭。
部署身份检查包含 affective_state，数据库备份自然包含新表，不重构 SQLiteStore/memories。

情绪更新先于 Replyer：最终回复失败时，已收到事件带来的 affect 可以保留，但不会形成
completed history / assistant Archive / Memory Formation。规划或事务失败不伪造更新。
同 source_event UUID receipt 防止重复积分；这不扩大原 Transport 重启去重承诺。

## Memory / 多用户

significance>=.8、有效 impulse>=.6、worth_remembering，且非计划/假设，才形成
SignificantAffectiveEvent hint。主回复成功归档后交给原 Formation，仍只有原有一次
extraction；hint 不是新证据，只提示关注 Archive。候选仍必须由原消息独立支持并通过
既有 evidence/save gate，不保存心情/关系数值，不直接将细小情绪变成 Memory。

Relationship 与 in-memory history 按 target 隔离。原 Memory 没有用户 ownership，
本轮不迁移：主要用户保留原 recall/formation；次要目标不读写主要用户长期 Memory。
这不是完整多用户 Memory 支持。

## 表达与 Reality Grounding

Planner 读取旧状态；更新后的 Emotion/Mood 用自然语言摘要投影。Selector 用关系、Mood、
Emotion 计分；陌生关系不选亲昵玩笑，低交流意愿降低展开权重，低精力/烦躁阻止不协调
的活泼临时风格，新状态也协调 planner 原语气意图。不朗读状态，不表演情绪。

romantic=false 不允许默认恋爱化。熟悉只影响语气，不创造共同历史。用户现实陈述、
角色世界事实、计划、假设、已发生事件要区分。没有共享现实空间接口，不能默认答应
现实来家里、见面、等人、赴约或共同物理行为。这是生成约束，不是模型绝不犯错的保证。

## 复用与未来边界

评估 [FAtiMA Toolkit](https://github.com/GAIPS/FAtiMA-Toolkit) 的 Appraisal/Emotion/
社会关系分离：Apache-2.0、研究应用型 C# 工具链，有机制参考价值，但完整运行时与
Python/SQLite/LLM 架构耦合成本高；不据此宣称其当前维护活跃度或服务可靠性。
采用 Design Reference，不复制代码。直接复用 Pydantic、sqlite3、Clock、Planner 和
Memory gate，无新依赖或情绪 Agent 框架。

未来 Vision/World/Time producer 可构造 Event；显著事件 hint 可供未来
ProactiveCandidate 构造者读取，本轮不调度、不主动发送。World event 更新自身 affect，
不自动修改与用户的关系。

## 性能 / 验证

主链仍两次 LLM。启用 appraisal 时 planner 输出预算从 768 提至 1280（上限增量 512），
8 秒网络操作超时、零重试不变。这不是固定多用 512 tokens，也不是总墙钟 SLA。
诊断提供实际 provider usage、独立 affective DB 耗时、规划与生成耗时。

本机五场景 fake smoke：Planner 新字段增加 328–350 字符；SQLite 读取+更新平均约
2.282 ms、最大 2.431 ms。字符不是 token；线上 token/平均延迟及比例未测，不编造数字。
所有测试使用临时 SQLite 和合成数据，不使用真实 QQ 隐私。

```bash
python scripts/run_affective_smoke.py
python scripts/run_natural_conversation_smoke.py
pytest
ruff check .
ruff format --check .
pip check
git diff --check
```

真实 QQ 行为仍需验收。语义误判 meaningful 的风险由小步长/cap 限制而非消除；
baseline、衰减、阈值是工程规则，不是心理学标定。未实现 Console 状态面板。

本轮离线回归：pytest 585 passed、3 skipped；Ruff check/format、pip check、
git diff --check 通过。22 个 smoke 全部正常退出，涵盖新 affective、自然对话、
QQ adapter/OneBot、Memory、State/Time/Life/World/NPC/Observation/Action、
Manager/Setup/Console。LLM 使用 fake；需模型的 Memory smoke 使用本机缓存模型并
禁用 Hugging Face 网络。此结果不代表真实 API、真实 QQ 或 Linux 部署验收。
