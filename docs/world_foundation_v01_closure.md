# World Foundation v0.1 Closure

## Verdict and scope

READY：单进程、单 SI-001、低频 Alpha 范围。Review P0 = 0，P1 = 0；不是多设备、任意并发或自然语言泛化可靠性的证明。

冻结包含 B4 Places、B5 Lightweight NPC Registry、B6 Observation、B7 typed move_to + Resolver、B8 Lazy World Time。无请求不运行世界模拟；显式读写与按需时间上下文保持原资源模型。

## Closure evidence

- WF-R01 已修：NPC 普通更新在 BEGIN IMMEDIATE 内读取最新行，验证后只 patch 显式字段；no-op/None/UTC/Place/immutable ID/rollback 边界保留。确定性交错测试覆盖 display_name 与 location、active、tags，均保留另一 writer 的更新。
- WF-R02 接受的已知语义边界：Observation 本身只读；assistant 复述可进入真实 Archive，再由 A3 Formation 判断。fake extractor 回归证明此集成路径及受控 reject，不保证真实模型不会发生 semantic provenance 错误；无新 evidence kind 或 Experience Store。
- WF-R03 已补正式 backup-before-action → action → restore → resubmit 测试。旧备份恢复移除回执后按恢复后的 Life 重新 resolve（replayed=false）；幂等只在当前 DB retained receipt history 内有效。
- WF-R04 已约束：低层 Life full upsert 保留但禁止 stale snapshot 普通部分更新，运行路径使用 initialize-if-missing / transactional patch；docstring 与回归测试同步。
- WF-R05 已澄清历史：A3 recall 失败限制被 A6.3 graceful degradation supersede；B5 未实现 Observation 是阶段事实，当前有限观察来自 B6。
- B8 failure regression：snapshot 异常丢弃本轮组合上下文、不使用旧时段；仅异常类型诊断，主回复及正常完成后流程继续，无额外 State/Memory/Action 写入。

## Freeze boundary

Autonomy、Experience Store、NPC cognition、NPC schedule、true scheduler、World Event、relationship evolution 是未来阶段，不是 v0.1 缺陷。本 Closure 不启动这些能力或 Developer Console，不修改 Character Data、Prompt 行为、A3 算法或部署配置。

## Verification scope

离线 pytest 与既有 B5/B6/B7/B8 smoke（B8 含组合场景）使用临时 SQLite、FixedClock、fake LLM；不访问真实 runtime DB、DeepSeek、SiliconFlow 或 QQ。Ruff、pip check 与 git diff --check 检查工程一致性；结果见本次 Closure 交付记录，不把 smoke 当作大规模性能或模型质量证明。

本次 Windows / Python 3.12 验证：464 tests passed；ruff check / format --check、pip check、git diff --check 全部通过。四个既有 smoke 均 exit 0 并确认临时数据库/目录删除。未提交或 push。
