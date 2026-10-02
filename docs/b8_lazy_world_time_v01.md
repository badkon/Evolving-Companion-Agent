# B8 — Lazy World Temporal Context v0.1

## 1. Purpose

本阶段冻结为“按访问计算当前世界时段”，不是世界模拟。只有 SI-001 是完整 Character；时间经过不产生生活、经历或记忆。

通用能力 Direct Reuse 既有 Clock、stdlib datetime / zoneinfo（Python PSF License）与已有 tzdata。依据 [Python zoneinfo 文档](https://docs.python.org/3/library/zoneinfo.html) 使用显式 IANA 时区转换；这些成熟组件已经用于 B1，无新增依赖、外部代码或网络运行需求，数据留在本地。固定分段为少量 Custom Implementation。调度框架需要 job、持久恢复和执行语义，当前没有对应需求；引入会增加依赖、耦合及迁移成本，故不采用。

## 2. Why No Scheduler Yet

没有要执行的 job。没有 APScheduler、sched、cron、Celery、Redis、后台线程、async task、timer 或空壳 Scheduler。B8 是可重算的时间上下文，不是 tick loop。

## 3. Shared Clock

复用 Clock / SystemClock / FixedClock，没有 WorldClock 或第二套 CharacterClock。snapshot 未传时间时读取注入 Clock 一次；显式时间不读取 Clock。拒绝 naive datetime，aware 输入统一转换到 UTC。

## 4. WorldTimeSnapshot

冻结 dataclass，服务返回如下字段：

| 字段 | 语义 |
| --- | --- |
| now_utc | UTC aware datetime |
| now_local | 配置时区的 aware datetime |
| timezone_name | 显式 IANA 名称 |
| day_period | morning / afternoon / evening / night |

快照仅是当前输入的派生结果，没有历史锚点、版本游标或角色经历。

## 5. Timezone

CLI / QQ 将现有 Character Seed 的 timezone 传入 WorldTimeService，当前为 Asia/Shanghai。这是单世界开发期配置，不读取设备本地时区、不推断用户/IP 位置，不新增 Setup / Manager 配置。

## 6. Day Period Rules

规则集中在 WorldTimeService.snapshot：06:00 ≤ local < 12:00 为 morning；12:00 ≤ local < 18:00 为 afternoon；18:00 ≤ local < 22:00 为 evening；其余为 night。中文投影为早晨、下午、傍晚、夜间。

这些是固定工程分段，不计算太阳、季节、天气或日出日落，不说明学校营业、灯光或睡眠。

## 7. Observation Integration

ObservationService 可接收 WorldTimeService，复用 capture_context 的同一 UTC stamp 派生时段，再附到冻结 ObservationSnapshot 的可选 day_period。Store 的 B6 一致读事务不变；时间计算不依赖 Store。

沿用 `【当前可观察环境】`，只增加简短时段和边界说明，不新增世界时间区块、不重复 B1 日期/精确时间，不投影 UTC、时区 metadata 或 schema 字段。未知位置仍可提供时段，但不推断地点、周围人物或活动。未配置该服务时 B6 原投影保持不变；查询失败继续省略本轮组合上下文，不用旧快照。

Closure 专项回归：已有成功快照后 WorldTimeService.snapshot 抛异常，本轮 Life/Observation 组合块全部省略，不复用旧时段、不伪造新时段；主 LLM/Archive/history 与成功后的既有时间记录、formation 继续。诊断和日志仅含异常类型，不含异常文本。B8 失败不额外修改 State、Memory、Action 或 Life；未改变既有 failure boundary。

## 8. Conversation Time Consistency

Conversation.send 读取一次 Clock。B1 Snapshot、B2 reconciliation、B6 observed_at、B8 Snapshot 使用同一个 now_utc，避免 21:59 / 22:00 跨边界矛盾。WorldTimeService 在该轮不再取时。

成功交流时间仍只在 assistant 归档成功后 best-effort 写入，但写入的是本轮共享时间，而非再次采样的完成墙钟；LLM 时延不计入该锚点。失败/History/Formation 边界不变。Archive / Memory 创建时间仍由原 Store 管理，不属于 B8 的 Clock 采样。

## 9. Persistence

无新表、列、索引、runtime 文件或迁移。WorldTimeSnapshot / day_period 不落库，不进入 history、Memory、Evidence 或持久 World State。没有 last_tick、scheduler cursor 或时间缓存。

## 10. Restart Semantics

同一 aware 时间 + 同一时区配置（同一时区规则数据）得到相同结果。重建服务无需恢复 tick 或 scheduler 状态；Store 仍按既有短连接方式关闭每次连接。

## 11. Offline Semantics

22:00 到八小时后的 06:00 可以从 night 变成 morning，但不表示睡了八小时、刚起床、度过一夜或去了学校。只计算当前时段，不从 last_interaction_at 推进世界，不补执行离线任务。

## 12. Clock Rollback

10:00 回拨到 05:00 可以重算为 night。服务没有可回退的持久状态，不写数据库、不更新 last_interaction、不倒退 State 锚点、不重放 Action / Event / Memory。原有 Conversation 的 B1 / B2 rollback 保护独立保留。

## 13. Character State Boundary

不修改 energy、attention、mood、social 或 activity，不修改 A4/B2 规则。Conversation 中既有 State reconciliation、Archive、成功时间及 Formation 仍可执行，不能把它们误归因于 B8。

## 14. Action Boundary

不创建 MoveToIntent，不自动移动，不修改 B7 receipt。位置唯一权威仍在 Life；receipt 是历史结果。B6+B7+B8 组合检查只由 smoke 显式提交测试动作。

## 15. NPC Resource Boundary

不改 NPC 位置、active 或 timestamps；无日程、通勤、睡眠、学校/工作时间。WorldTime snapshot 为 O(1) 时间转换和分段，闲置 CPU 增量约为 0；后台 task/thread/timer、API/model call、SQLite write 均为 0，无缓存/后台刷新。这是实现结构边界，不是实测性能 benchmark。

## 16. Known Limitations

只有粗粒度当前时段，不具备 place open/close、天气、灯光、睡眠、课程表、路径、活动或离线世界模拟。观察不是知识、经历或记忆；既有 A3 对 assistant Archive 的 Formation evidence 限制仍存在，没有新增自动 World Memory hook。

离线 pytest 使用 FixedClock、临时 SQLite、fake LLM，检查全部分段边界、跨日/离线/回拨、未知位置、同轮共享时间、无数据库变更和模型/后台工作边界。`python scripts/run_world_time_smoke.py` 包含 B8 单阶段及 B6+B7+B8 组合场景：23:00→07:00、固定位置/人物、重启一致；显式 A→B、fresh Observation、时段变化、重启后旧动作幂等 replay。退出验证临时数据库/目录删除。既有 observation / world_action smoke 继续离线验证；不访问真实 runtime DB、QQ 或模型服务。

## 17. Future Scheduler Requirements

真正引入 NPC schedule、scheduled World Event、营业状态或后台通知前，必须另行决定：derived NPC location 是否 authoritative；显式 Action 如何覆盖 schedule；override 持续多久；是否需要持久 World Event；离线期间是否补执行；restart recovery；job idempotency。

本版仅记录这些问题，不实现 Scheduler、Experience Store、自主行为、Developer Console 或未来占位模块。
