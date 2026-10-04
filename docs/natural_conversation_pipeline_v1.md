# Natural Conversation Pipeline v1

本轮实现的是正式回复生成链，不是新的 Character、Memory 或 World 系统。
用户已报告原部署链的 First Real Conversation 通过；**新管线仍需真实 QQ 行为验收**。
代码/合成 smoke 通过不等于自然度达到 MaiBot，也不证明模型永不反问或编造。

## 1. 生产路径与边界

```text
CLI / QQ adapter → Conversation（先保存 user Archive）
  → 原有 State / Time / Life / Observation / Memory context
  → PromptBuilder（原有身份、事实边界和 history）
  → ReplyPlanner → ReplyGuidance → ExpressionIntent
  → ExpressionSelector → 0–3 habits + optional temporary style
  → Replyer（base style + reply_reference + target）→ LLM
  → assistant Archive → history → best-effort 时间记账 / Memory Formation
```

`runtime.create_conversation()`（QQ 与 none Core 共用）和开发 CLI 通过
`create_reply_pipeline()` 默认接入 natural。none 启动只构造资源，不调用规划或回复 API。
底层 `Conversation(..., reply_pipeline=None)` 保留原单次 complete 协议，供 legacy、
既有定向 smoke 和嵌入调用；不自行读取环境或创建额外 provider。不存在第二套归档/记忆业务流程。

在 `config/si.env`（开发 CLI 用 `.env.local`）或显式环境中设置：

```ini
SI_REPLY_PIPELINE=natural
# 对照旧路径时改成 legacy；未配置默认为 natural
```

重启 Core 后生效；Console 不新增 UI，现有配置保存保留未知字段。非法值启动失败，
不偷偷切换 legacy。现有 offline health 尚不单独验证此开关。

## 2. 规划与 Replyer

- `ReplyGuidance` 使用 Pydantic 验证 focus / reply_act / scene / tone / prefer / avoid /
  reply_reference；free text 有长度上限，动作/场景/语气/标签用有限词表。相互冲突的标签拒绝。
- `ExpressionIntent` 是独立只读对象，从 guidance 投影；规划和习惯只是瞬时表达信息，
  不写 Character Data、Archive、Memory 或 State。
- planner 使用身份与上下文边界、最近最多 12 条消息、明确 target。
  “不经常反问”的近期表达偏好对后续轮次仍有效；缺信息的明确请求可以澄清。
  不把好奇与维持聊天混同，不从用户疲惫推断玲也疲惫。
- `reply_reference` 是重点和依据，不是最终答案；不创建事实或授权行为。
- `Replyer` 原样接收原 PromptBuilder 的完整身份、Memory/State/Time/Life/Observation
  区块和当前 history，追加一份紧凑表达 brief。原始当前 user 消息仍在列表最后。
  输出不做字符串黑名单、删问号、补长或拆气泡。
- `ReplyTarget` 明确区分 user_message 和 trusted contextual_trigger。
  user target 必须与最后一条消息相符。未来调用者可提交上下文触发，但本轮没有事件生产、
  定时器、主动发送或 World Runtime；不能由聊天文本自行升级为系统触发。
- 未记录活动不能补成“在发呆”；地点不等于活动，观察不等于知识/经历，
  Character ≠ LLM、Model Capability ≠ Character Capability 等原边界保留。

## 3. 真实表达选择与风格

`expression_habits.py` 内置 **24 条** SI 独立撰写的 situation/style 配置，覆盖招呼、
普通陈述、状态分享、角色状态、疲惫、开心、抱怨、成果、失败、奇怪事物、项目、游戏、
学习、代码、短句、在吗、持续聊天、沉默、问题、澄清、兴趣、玩笑、轻微吐槽、结束话题。
它们描述表达方向，不是固定答句或伪造的角色经历。

选择步骤：avoid 标签硬排除 → reply_act 约束 → scene 或多个 prefer+keyword 匹配 →
scene/prefer/keyword/weight 计分 → 上轮已选习惯降权 → 加权无放回选择 0–3 条。
没有适配候选就为空，匹配关键词不能跨动作强行选好奇或调侃。测试可注入 Random 固定种子。
配置可通过 selector 构造参数替换；未来学习/统计/向量候选不在本轮实现。

Base Reply Style 独立于 Character Identity：日常口语、长短随语境、通常简短，
不用提问、客服邀请或咨询式复述维持聊天；保留少女感、独立判断、玩笑、兴趣和必要澄清。
TemporaryStyle 有安静、轻快、随意、玩笑、利落五种轻度修饰：按 tone/avoid 筛选，
25% 概率选取，使用后两轮冷却；clarify 不叠加随机修饰。
这不是情绪状态，也不会让角色声称自己正在做某件事。

## 4. 成本、失败和隐私

- 正常 natural：**2 次**回复侧 LLM 调用（规划 1 + 最终回复 1）；legacy 为 1。
  现有 Memory Extraction/Consolidation 自身的调用不变，不计入这两个数字。
  这里是逻辑调用次数；最终回复客户端既有的网络重试策略保持不变。
- planner 复用当前 LLM endpoint/model（默认 deepseek-flash），单独客户端设置
  max_output_tokens=768、timeout=8 秒、max_retries=0；不重复刷规划。
  HTTP timeout 是网络操作超时，不宣称严格总墙钟 8 秒 SLA。
- 规划保留完整权威区块，超过 20,000 字符则显式降级；history 最多 12×800 字符，
  target 最多 6,000 字符，截断带说明，不推断省略内容。最终 Replyer 保留原完整上下文。
  这些是规划输入预算，不是回复字数截断；规划输出另外限制 6,000 字符和 schema 字段长度。
- Replyer 增量主要是一份 brief 和所选少量 habits，不把 24 条全部塞入每轮 Prompt。
- `ReplyDiagnostics` 提供规划/选择/生成耗时、规划输入字符、Replyer 增量字符、
  provider 实际返回的 planner prompt/completion token 数；无 usage 就是 null，不估算冒充实测。
  DEBUG 仅输出有限标签、内置习惯 ID、风格 ID、数字和异常类型；不输出 focus、reference、
  完整聊天、QQ ID、key 或异常正文。
- 规划 API/JSON/校验失败：warning + planner_error，采用明确保守 guidance、空 habits、
  无临时风格，让最终生成依据原上下文继续，仍可必要澄清。不是正常路径的静默功能删减。
  最终 LLM 或 assistant Archive 失败仍不形成 completed history / Memory。
- 规划客户端及正式入口的回复客户端由现有 ExitStack 关闭。不改 Store、Transport、
  Memory schema/provider/formation、Character Seed 或 World 状态语义。

## 5. MaiBot 参考与复用评估

实际阅读公开仓库 commit `a1ceb74728512d9dcd1fb29a2bdadb91ee909ad4`：

- [reply tool / guidance / expression_intent](https://github.com/Mai-with-u/MaiBot/blob/a1ceb74728512d9dcd1fb29a2bdadb91ee909ad4/src/maisaka/builtin_tool/reply.py)
- [候选池、intent/reference 查询、0–5 子选择](https://github.com/Mai-with-u/MaiBot/blob/a1ceb74728512d9dcd1fb29a2bdadb91ee909ad4/src/chat/replyer/maisaka_expression_selector.py)
- [基础/临时风格、reply_reference、target 与 history](https://github.com/Mai-with-u/MaiBot/blob/a1ceb74728512d9dcd1fb29a2bdadb91ee909ad4/src/chat/replyer/maisaka_generator_base.py)
- [Replyer 模板](https://github.com/Mai-with-u/MaiBot/blob/a1ceb74728512d9dcd1fb29a2bdadb91ee909ad4/prompts/zh-CN/maisaka_replyer.prompt)

MaiBot 是维护中的完整聊天系统，与目标高度相关，但其模块绑定自有会话、SQLModel/
SQLAlchemy、插件、表达学习和子代理运行时；GPL-3.0 实现不能当作无授权限制的代码粘贴。
结论：**Design Reference**，仅参考机制，SI 代码/配置/提示词独立撰写，不移植其源码。
已评估复用 SI 的 Embedding/Reranker：24 条内置配置不需要新增远程向量调用或本地模型，
语义规划+加权候选匹配足够实现当前功能；保留 Memory provider 原边界，也更容易替换。
Pydantic/现有 LLM adapter 直接复用；选择器使用标准库，无新依赖或框架。

状态中的 EQUIVALENT 指机制层面的等价，不是已证明自然语言效果相等。

| MaiBot capability | SI implementation | Status | Difference | Reason |
| --- | --- | --- | --- | --- |
| personality / identity | 原 Seed→Projector→PromptBuilder | EQUIVALENT | 保留 SI 身份/数字存在，不复制 MaiBot 人设 | Character authority |
| reply_style | 独立 BASE_REPLY_STYLE | EQUIVALENT | SI 中文口语与关系边界 | 保持人格 |
| multiple_reply_style | 五种修饰、概率、上下文约束与冷却 | EQUIVALENT | 不每轮采样、不随机变人格 | 稳定性 |
| reply action / planning | 结构化 ReplyPlanner | EQUIVALENT | 只规划表达，不执行工具 | 无 ThinkFlow/Agent loop |
| reply_reference | bounded guidance→Replyer | FULL | 保留非最终回复语义 | 重点与依据 |
| expression_intent | 独立六字段模型 | FULL | 标签受限，自由 focus 保留 | 验证与隐私 |
| expression selector | 上下文计分、加权 0–3 选择 | EQUIVALENT | 不用向量池或第二次选择 LLM；MaiBot 可选 0–5 | 24 条内置池、只新增一次 LLM |
| expression habits | 24 条内置 situation/style | EQUIVALENT | 不接表达数据库或自动学习 | 本轮允许内置来源 |
| contextual reply target | 显式 ReplyTarget | EQUIVALENT | 私聊末条消息/受信上下文，无群聊 msg_id 路由 | 当前 Transport 范围 |
| replyer | 独立 Replyer | EQUIVALENT | SI 事实边界和原上下文复用 | 不破坏 Memory/World |
| context history | 原 history + 规划最近窗口 | EQUIVALENT | 无群聊多发言人压缩 | 既有私聊范围 |
| expression learning / usage DB / vector pool | 未实现 | NOT IMPLEMENTED | 只有瞬时上轮降权，无持久学习/统计 | 明确不在本轮要求 |
| autonomous planning / proactive sending | 仅 trigger 输入接缝 | NOT IMPLEMENTED | 不发消息、不调度事件 | 明确 Non-goals |

## 6. 验证与恢复

`tests/fixtures/natural_conversation_cases.json` 是 9 条人工合成案例：8 条指定场景 +
跨轮表达偏好。测试覆盖候选差异、避用/空选择、临时风格冷却、target、上下文保留、
两阶段 LLM、规划失败、归档完成边界、QQ 去重、factory 默认/legacy 和资源关闭。

```bash
pytest
ruff check .
ruff format --check .
pip check
git diff --check
python scripts/run_natural_conversation_smoke.py
# 显式真实合成观察（会调用 API），临时 DB，不使用生产 Memory：
python scripts/run_natural_conversation_smoke.py --real-llm
python scripts/run_natural_conversation_smoke.py --real-llm --pipeline legacy
```

fake smoke 仅证明接线，不证明 planner 能正确理解自然语言。
真实观察打印案例、目标、回复和安全成本诊断，不按固定答句自动评分。
没有新的真实 QQ / API 自然度验收证据时必须注明：**Real QQ behavioral validation required.**
规划分类和自然语言执行仍依赖模型；不保证彻底消除 chatbot 风格。

中断恢复时先检查 git status/diff、这些模块及最近测试失败，再继续未完成部分；不要覆盖重做。

### 本轮验证记录（2026-10-04）

- pytest：559 passed，3 skipped（Windows 下的 Linux shell / process 测试及 bash 不可用检查）。
- Ruff check / format、pip check、git diff --check 通过。
- 21 个相关 smoke 通过：natural、QQ adapter、OneBot、Memory injection/formation/
  consolidation/provider、State/transition/event、Time/reconciliation、Life、World、NPC、
  Observation、Action、World Time、Manager、Setup、Console；另验证 legacy natural-smoke 路径。
- 主对话 LLM 均为 fake；Memory 的旧 smoke 使用本机 BGE 模型并有 Hugging Face 元数据访问，
  不能把这部分描述为严格断网测试。未调用真实 DeepSeek/SiliconFlow API；数据库均为临时库。
- 新 smoke 9 个合成目标测得规划输入 3,309–3,405 字符、Replyer 增量 832–882 字符。
  fake 规划+选择通常不足 1 ms；这只是本地处理数据，不代表真实 LLM 延迟。
  实际 token / API 延迟本轮未测，真实模式会打印 provider usage 和分段耗时；不填造数字。
- 真实配置、secret、runtime DB 和日志未加入改动；没有 commit/push。
