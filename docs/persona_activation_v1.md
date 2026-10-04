# Persona Activation & Character Distinctiveness v1

## 数据路径与范围

已有 YAML Seed → CharacterProjector（core / voice / traits）→ 本地候选预筛选
→ 同一次 Planner + Appraisal + Persona Relevance → 现有 affect 更新
→ ExpressionSelector → Replyer。正常主回复仍为两次逻辑 LLM 调用。
Memory 原有抽取/整理调用独立，不计入这两次。没有新依赖、人格表、人格演化或 Console 功能。

`description` 保留给 legacy；自然路径只保留身份、行为、认知、能力等核心边界与紧凑 Voice。
Voice 由 Seed 的基础性格与社交倾向生成；喜欢/厌恶在相关轮次通过 active traits 补充。
Character Voice 表达“这个角色如何反应”；Base Reply Style 仍只负责口语组织和节奏。
没有复制人物设定，也没有把能力或模型知识变成经历。

## 特征选择与规划

- trait pool 从已有喜欢、厌恶、基础性格、社交倾向生成；喜恶 ID 按类别和内容稳定生成。
  这些是瞬时投影 ID，不是身份 UUID 或新的持久化记录。
- 本地 selector 使用类别、主题同义线索、当前文本、明确接续时最近最多三条用户消息、
  已有关系阶段。最多 6 条候选；陌生人不能得到熟人特征。普通问候、时间问题可无候选。
  线索表只路由已有设定，不因关键词新增偏好，不调用网络、embedding 或 LLM。
- Planner 结合当前 scene / reply_act / 语境作最终选择：0–3 个候选 ID，
  `persona_relevance = active_traits + strength + reason`。即便主题匹配，客观任务仍可选空集。
  ID 唯一、属于当轮候选；空集必须配 none，非空必须有强度。
- Persona Contrast 在同一次规划中内部比较通用助手回应与有依据的角色反应，
  只将相关差异体现于 prefer / tone / reply_reference / active_traits，不输出对比过程。
- Replyer 由服务端将 ID 解析为 Seed-derived 语义，不信任模型自行编写 trait 内容；
  不注入未选偏好。medium/high 时轻量自检是否过度泛化，但不得强行吐槽、反问或加戏。

## 优先级与边界

事实/身份边界 > 稳定角色立场 > 关系距离 > 当前情绪心情 > 表达习惯/临时风格。
开心、熟悉、礼貌、用户推荐和 Memory 往事均不意味着稳定厌恶发生改变。
关系仍只调社交距离；affect 仍由现有确定性规则更新，不接受 traits 作为状态增量。
24 条习惯不删；active traits 非空时最多使用一条习惯，停用本轮临时风格。
原低能量/烦躁/陌生人限制保留。无关轮次可以一句简单回复，不补长、不强演人格。

Grounding 不放宽：可用自然口语说明限制，但不虚构共同在场、活动、亲历或现实见面约定。
romantic=false、Character ≠ LLM、模型能力/知识 ≠ 角色能力/知识等规则保持。

## 失败与隐私

规划缺少 persona_relevance、选择未知 ID、重复/超量或强度矛盾时，走既有安全降级，
无重试、无第三次调用、不应用失败规划的 appraisal、不携带上一轮 traits。
降级不凭空激活候选，不编造喜恶或鼓励接受未经确认的提议。此时角色辨识度可能下降。
诊断日志只增加候选/已选数量、有限强度与本地选择耗时；不写 trait 内容、reason 或用户文本。
运行内存中的 last_* 只用于当前轮诊断，不是 Memory 或 evolution。

## 验证与成本

`tests/fixtures/persona_activation_cases.json` 有 37 个合成场景，覆盖 A–Q 共 17 类。
测试验证候选来源、零选择、稳定喜恶在多语境中的可用性、关系/affect 优先级、两调用链路、
Prompt 接线、校验降级、Grounding 与原有 Conversation 完成边界，不断言真实模型固定台词。

```bash
python scripts/run_persona_distinctiveness_smoke.py
python scripts/run_natural_conversation_smoke.py
python scripts/run_affective_smoke.py
```

Persona smoke 全离线、无数据库；打印候选选择、guidance、合成回复路径及字符/本地耗时统计。
字符对照为当前版本“完整角色上下文且无候选规则”与“核心/Voice + 候选规则”，
两者共用新 schema，因此该差值不包含新增 schema 字符。字符不等于模型 token。
真实 token、网络延迟及真实 QQ 辨识度仍需人工验收，fake 成功不证明真实语义质量。

本地词义线索不是完整语义理解：隐晦指代、超过短上下文的接续、未覆盖同义表达可能漏选；
Planner 也可能选错或放弃候选。稳定立场目前通过可信投影与两阶段指令约束，
不是对任意模型输出的数学保证；不增加事后 Judge 或改写回答。
