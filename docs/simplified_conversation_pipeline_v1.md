# Simplified Conversation Pipeline v1

## 配置与结构

```ini
SI_REPLY_PIPELINE=natural_simplified
```

结构为 **State → Relevant Context → Tiny Planner → Replyer**。
默认 `natural` 与显式 `natural_full` 都使用既有完整管线；`legacy` 保留原单次生成。
现有部署不自动切换。该配置由现有工厂在 Core 创建时读取，修改后重启 Core 生效。

## 复用与旁路

直接复用当前 Character Projection、Persona 主题筛选、Pydantic、LLM Adapter、Clock、
Conversation 的 Memory recall / State / Time / Life / Observation，以及 AffectiveStore
的 snapshot / Appraisal apply / significant-event gate；不引入外部框架或复制运行时。
没有新的依赖、数据库表、额外状态写入或状态初始化路径。

两种自然模式共享 user Archive → 收到事件 → 规划/Appraisal → 既有状态更新
→ Replyer → assistant Archive/history → 原有 Memory Formation 的完成边界。
关系仍按目标隔离；无 Memory ownership 的次要目标仍不能读写主要用户 Memory。
回复生成失败可保留已收到事件的 affect，但不会形成 completed history 或新 Memory。

simplified 不调用 Expression Intent、Expression Selector、24 Habits 或 TemporaryStyle；
也不初始化 Selector 或它的习惯集合。
这些代码及 full 的选择行为保留。基础格式规则单独压缩，不借它定义人格。

## Relevant Context

`ConversationState` 是本轮快照及可选粗粒度生活安排的只读容器；Builder 不访问数据库、不调用模型。
使用核心身份/认知/行为/能力边界、紧凑 Character Voice 和最多 3 条相关 traits。
Persona preselection 的类别/主题/接续/关系能力复用，最终本地取最多 3 条；
不再生成 persona_relevance、prefer/avoid 或角色对比表达字段。
额外的“你也试试”等接续线索只对 simplified 启用，不改变 full 默认筛选。

- recent context 最多 6 条，每条 800 字符，截断带标记，不推断省略部分。
- Memory 使用原 recall 已通过 gate 的最多 3 条，每条最多 800 字符加截断标记，保留类型与来源；inferred 不等于事实，
  Archive、Memory、亲历和 Character Data 不混同。不会再次 retrieval 或写 Memory。
- Relationship 摘要当前 stage、familiarity、comfort、formality 及 romantic=false，
  用自然语言表达熟悉程度、自在程度与客套程度，不注入 UUID、数值或别人的私人原因。
- Affect 摘要当前 Mood 与最多两类明显感受，当前轮使用新 Mood/Emotion、更新前关系，
  不生成独立 style 指令，不覆盖稳定立场。
- Time 使用已有 Clock 快照；Life/World 只在相关询问中提供已记录生活关联及匿名观察。
  `Mini Life Context v0.1` 每轮 simplified 只计算一次；Builder 在相关生活询问或短接续中
  注入 now/next/today 的紧凑摘要。问候、代码成果等无关轮次省略摘要，不强行提起自己的活动。
  由 aware 时间、Seed 时区/身份/喜好及集中时间块即时推导；显式活动和观察优先。
  工作日准备后有学校安排，中午休息、约14:00后自由时间；周末晚起，晚间个人时间，夜间休息。
  自由活动按 internal_id + 本地日期 + 时间块稳定选择已有故事/游戏/音乐喜好。
  次日只说明通常学校安排或自由时间，不生成课程名，不处理假期/学期。
  这是虚拟世界粗安排，不写 Life State/位置、不证明活动执行，不生成过去经历或现实到访。
- facts 明确保留没有共享现实物理空间/实时用户摄像头接口的当前事实。
  用户说开接口不能自动改变系统能力，不随机制造 daily feel。

已有 B3 Life、A4 State 和 B8 Time/Observation 已满足本轮需要，因此没有新增 Life State。

离线生活摘要检查：`python scripts/run_mini_life_smoke.py`（不访问数据库或 API）。

## Tiny Planner 与 Replyer

表达计划严格只有四字段，Pydantic 拒绝旧控制字段和非布尔 ask：

| 字段 | 语义 |
| --- | --- |
| focus | 当前主要回应点 |
| stance | 有依据的角色立场，允许 null |
| boundary | 当前相关事实限制，允许 null |
| ask | 严格 boolean，默认规划倾向 false |

启用 affect 时，同一 JSON 还包含原 AffectiveAppraisal 的必要事件字段；这些字段仅用于
现有状态事务，不传给 Replyer，不扩展为表达控制。正常主回复仍两次逻辑调用；
原 Memory Formation 的模型调用单独存在，不计入这个数字。
Planner 原 timeout、零重试和输出预算保持。无效规划沿用安全降级：不更新 appraisal，
保守四字段计划、ask=false，仍尝试最终回复，不增加第三次调用。
Tiny Planner 当前 target 最多 6,000 字符加截断标记，Replyer 仍收到完整当前消息。

Replyer 只收到核心角色/Voice、相关 traits/history/Memory、关系/affect 摘要、生活世界事实、
四字段计划及短格式规则。没有 scene/tone/prefer/avoid/reference/intent/habits/style。
输出生成要求：最多 3 句，默认 1–2 句、单段短句，不独立尾部反问、不用“——”制造结构，
不为续聊提问，非技术聊天不用 Markdown。输出不经黑名单、截断或自动拆句改写。

Grounding 压缩为四字段中的 boundary 和少量不可被规划取消的事实约束。
整段旧 GROUNDING 仅在必要 Appraisal 规划中使用，不传给最终 Replyer。
模型用自己的口语说明限制；熟悉不创造共同历史、现实赴约或恋爱关系。

## 调试、测试与性能

`last_relevant_context.inspect(environment)` 为显式本地调试提供当前选择内容，复用现有
secret redaction；不自动写日志、不进入聊天。不要对私人数据未经授权开启检查。
`TinyPlanner.inspect_reply(last_relevant_context, last_tiny_plan, environment)`
可显式查看该轮实际 Replyer 消息，包括四字段计划和 boundary；同样先脱敏。
`last_tiny_plan` 仅留本轮四字段，不包含 Appraisal；每轮清空，失败时展示实际保守降级计划。
默认诊断只包含模式、计数、耗时和错误类型，simplified 的旧表达诊断字段留空。

53 个 synthetic scenarios 覆盖短句、分享、喜恶、劝说接续、关系/affect、Memory、
事实/技术问题、身份/认知、虚拟世界与现实接口边界等。测试验证结构和状态完成边界，
不依赖真实 API，也不按固定角色台词测试自然度。
Life 场景实际复用已有 MiniLifeService，包含当前活动、忙闲、晚间/次日安排、
相关接续及无关场景；A/B 使用相同 Character 时区与本轮虚拟环境快照。

```bash
python scripts/run_simplified_conversation_smoke.py --pipeline natural_simplified
python scripts/run_simplified_conversation_smoke.py --pipeline natural
python scripts/run_simplified_conversation_smoke.py --pipeline both
```

两种模式使用同一合成输入和独立临时 SQLite，fake Planner 返回脚本化计划，
输出 selected context、plan、字段/控制层数量、输入字符和 fake reply，退出后删除数据库。
低能量等前置状态通过现有 AffectiveStore API 的合成事件构造，不编辑 SQL payload。

本机同 53 场景 A/B：Planner 平均字符 natural≈8,606、simplified≈4,863（约减少43.5%）；
Replyer natural≈3,477、simplified≈1,329（约减少61.8%）。所有场景两阶段输入字符均下降。
Builder 平均约 0.06 ms。字符不是 provider token，未测真实 tokens 或 API 延迟；
脚本化 ask/stance 不能证明真实模型遵循或“更自然”。

本轮回归：pytest 743 passed、3 skipped；Ruff check/format、pip check、git diff --check 通过。
Natural、Affective、Persona、Mini Life、simplified 与 A/B、Memory provider integration、
QQ adapter、OneBot loopback、Manager、Setup、Console 共 12 项离线 smoke 通过。
Runtime/Memory/Transport/UI 回归包含在完整 pytest 中；未调用真实模型 API，未做真实 QQ 验收。

## 限制与验收

主题线索与短 history 可能漏选远距离关联；Memory 只取已召回的前三条，不添加新 rerank。
Life 相关性同样使用少量本地词义线索，可能漏掉隐晦指代；不引入语义分类模型。
生成约束不是任意 LLM 输出保证，特别是句数、soft grounding、必要提问与稳定立场；
没有新增事后 Judge。必要 appraisal 和失败恢复语义仍须一起观察。
真实 QQ 验收应比较同输入的熟人自然度、系统说明腔、自动追问、立场一致性、
现实见面/摄像头边界、回复长度和实际 token/延迟，再决定是否切换默认模式。
