# B3 — Personal Life Scaffold v0.1

## 1. Personal Life Context Definition

Personal Life Context 保存当前生活结构、阶段、生活身份和粗粒度地点引用，作为 Character 的长期生活上下文。角色基础人格/边界仍由 Character Data 管理；运行中的 Life Context 是独立的当前事实记录。

实现复用已有 Pydantic 校验、stdlib SQLite、Clock 和 partial-update 存储方式。一个小型 `character_life.py` 模块承担模型与显式服务，通用能力采用 Direct Reuse，项目特有边界采用 Custom Implementation。没有引入外部代码、ORM、生活 Agent 框架或位置实体框架；既有依赖的许可与运行数据边界沿用当前项目方式。本地存储与简单记录足以覆盖本阶段；完整世界框架会增加依赖、耦合与后续迁移成本。

## 2. Life Context vs State

Location ≠ Activity。`current_location_reference="学校"` 属于 Life Context；`current_activity="看漫画"` 属于 Character State。LifeService 更新不修改 State，不从聊天文本推断地点，也不迁移或重写已有 activity 字符串。B2 reconciliation 继续只处理短期 State。

## 3. Life Context vs Memory

Life Context 是当前系统记录的生活上下文，不自动成为 Character Knowledge 或 Lived Memory。更新家、学校或位置不写 Memory，也不制造曾经居住、到校或交往的经历。未来如需形成记忆，需要明确的证据与独立流程。

## 4. Life Context vs World State

学校引用只表示 Character 与学校有关；不说明开放状态、天气、课程、学生、教室或其他客观世界事实。当前不创建地点实体、地图、地址或 NPC。World State 留给 B4 的独立任务。

## 5. Schema

`CharacterLifeContext` 为冻结、拒绝额外字段的 Pydantic 模型，字段如下：

| 字段 | 类型与语义 |
| --- | --- |
| character_id | identity.internal_id UUID；不是 development_id |
| life_stage | student / worker / unemployed / unknown |
| home_reference | 可空字符串，关联的家 |
| school_reference | 可空字符串，关联的学校 |
| primary_area_reference | 可空字符串，主要生活区域 |
| current_location_reference | 可空字符串，当前已明确记录的位置 |
| current_role | 可空字符串，当前生活身份 |
| updated_at | UTC aware datetime，最后实际变化时间 |

非空 reference / role 不能是空字符串或全空白；清空使用 None。`life_stage` 未确定时使用 unknown。

## 6. Persistence

SQLite `character_life_context` 每个 internal UUID 一行；使用现有 runtime DB，不创建 history table。升级旧库时以 CREATE TABLE IF NOT EXISTS 增加新表，保留原 State、Archive 和 Memory。每次存取均复用现有短连接并显式释放。

初次读取无记录时建立默认上下文。后续优先读取持久化记录，修改 seed 或重启不会覆盖它。当前服务按单 Character 配置绑定 UUID，不处理并发多设备同步。

## 7. Initialization

来源是 `docs/character/character-design.md` 第 3 节“学校与生活阶段 / 居住与经济”和第 6 节“World & Society”。这些已有设定说明学生阶段、学校、住宅区域的家，以及主要教育生活区。

已将上述最小信息录入 `si_001.yaml` 的 `initial_life_context`：student、学生、住宅区的家、学校、教育生活区。它只是首次初始化配置，不是每轮投影的权威运行数据。当前设计没有指定此刻的位置，因此 `current_location_reference=null`，不假定在家或学校。不加载作息、年级、课程、家庭成员或具体地址。

旧 seed 未提供此块时默认为 unknown / None，不从模型知识补全。CharacterProjector 仍只投影原基础角色语义，当前 Life Context 经独立区块进入 Prompt。

## 8. Prompt Projection

Conversation 每轮读取已存 Life Context，PromptBuilder 追加 `【当前生活上下文】`。只显示已知的中文语义；unknown / None 不打印，全空上下文不增加区块。UUID、UTC 时间戳、updated_at 和 schema 字段名均不进入区块。

区块约束模型不得凭生活关联编造课程、天气、人物、行程或过去经历；没有已知位置时，不推断当前位置。既有 A1/A2 对话规则、三条中性示例、Time / State / Memory 区块语义保留。

## 9. Event Integration

本阶段没有增加 `location_changed` Event。现有 CharacterEvent 的 payload 与处理结果专用于 Activity / State，扩展它会涉及结果与 payload 类型。按照本任务允许的最小路径，使用 `CharacterLifeService.update_life_context()` 作为显式更新接口；未来 Event 可委托它，但当前没有 Event → Life 的执行分支。

CLI 支持 `/life`、`/life location 家`、`/life location 学校`、`/life role 学生`；字段值 `none` 明确清空 location / role。这些是本地开发操作，不调用 LLM，也不自动产生事件、State 或 Memory。

Partial update 中省略字段保持原值，显式 None 清空 nullable 字段；无变化不写回、不刷新时间。变化时间来自注入 Clock；时钟早于已有 updated_at 时，显式更新报 clock_moved_backwards 且保留原记录。

## 10. Known Limitations

没有自动生活、日程、自主活动、世界模拟、NPC、位置实体系统、GPS、房间层级、学校课程、自动时间驱动迁移或离线生活。Time Model 提供时间，但不会自动改变 life_stage、位置或 role。Life Context 是粗粒度、显式维护的当前事实，不表示已执行活动或移动路径。

## 11. Future Work

World binding、真实位置变化事件和活动驱动更新需要后续明确设计。本阶段不实现 scheduler、sleep/wake、meals、pathfinding、关系/人格演化或 LLM Life inference。

## Verification

`python scripts/run_personal_life_smoke.py` 完全离线，使用临时 SQLite 与 FixedClock，检查默认值、显式位置更新、重启、+12h 不自动变化、Prompt 投影和 State/Memory 边界。pytest 同样不调用真实 API 或下载模型。
