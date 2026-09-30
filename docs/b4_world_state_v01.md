# B4 — World State v0.1

## 1. World Entity Definition / Place Definition

World Entity 是有稳定机器身份的外部对象；本版只实现 `entity_type=place`，直接使用 WorldEntity，不建立实体继承层。Character、World Entity、Place、World State、Life Context 与 Character State 语义独立。本阶段只有静态地点基础，不是完整世界模拟。

## 2. Stable Identity / Seed World Data

`data/worlds/si_world.yaml` 固定保存四个 UUID：教育生活区、住宅区、住宅区的家、学校。来源为 Character Design 第 3 节居住/学校和第 6 节教育生活区；不添加海边、灯塔、商业区或其他当前不需引用的地点。名称是展示文本，不是主键；允许重名。重启不生成 UUID，也不从名称或 Character ID 派生 UUID。

## 3. Parent Semantics

家属于住宅区，使用单 parent。其他 parent 未额外填充。parent 只表示 containment/belonging，不表示路线、距离、邻接、行政级别或坐标。Seed 校验拒绝缺失 parent、重复 ID、自引用及循环；SQLite 自引用 FK 采用 deferred 检查，支持 child 在 parent 之前插入。

## 4. Schema / Persistence

`world_entities` 字段：entity_id UUID、entity_type（仅 place）、canonical_name（非空）、parent_entity_id（可空 UUID）、description（可空）、created_at / updated_at（UTC aware）。Pydantic 冻结模型拒绝额外字段；SQLite 保存 UUID 文本与 UTC ISO 8601，约束 entity_type、名称及 parent FK。没有名称唯一约束，没有 history 表。

首次初始化 seed → DB；之后 DB 是 runtime source of truth。按 ID `ON CONFLICT DO NOTHING`：已有实体内容不覆盖，新 seed ID 可补入。初始化与读取不形成 Memory。未提供 runtime rename API；未来明确开发操作可改名称但不能改变 identity。

## 5. Reuse Evaluation

通用校验/持久化直接复用现有 Pydantic、PyYAML、stdlib sqlite3；Life 绑定和迁移语义为项目特定实现。参考 [Pydantic 模型文档](https://docs.pydantic.dev/latest/concepts/models/) 与 [SQLite FK 文档](https://www.sqlite.org/foreignkeys.html)。这些方案已在当前仓库使用，无新增依赖/网络运行需求，许可沿用现有依赖（Pydantic/PyYAML 为 MIT；SQLite 为 public domain）。本任务不需要 graph/ECS/ORM 框架；引入此类框架会增加耦合和迁移成本，不能解决当前最小引用需求。没有复制外部代码。

## 6. Character Life References / B3 Migration

Life 中四个字符串引用改为 `home_entity_id`、`school_entity_id`、`primary_area_entity_id`、`current_location_entity_id`（UUID | None）。Character 主键仍为 `identity.internal_id`，不与 World UUID 或 development_id 混用。Seed 初始 Life 配置也使用固定 World UUID；新记录初始化前应先初始化 World。

兼容初始化以 ALTER TABLE 增加四个 FK 列和 `world_references_migrated` 标记，不改 Memory / State schema。World seed 初始化后一次性迁移未处理行：只对 seed canonical_name 精确且唯一匹配。未知、近似或重名值不猜测，新 UUID 列为 None，并返回 `unresolved_legacy_place` 诊断。旧 `*_reference` 列保留原文本，便于人工检查，不再读取为 runtime identity，不进入 Prompt。updated_at 保留；初始化完成标记防止重启将显式清空值重新填回。

迁移和 seed 插入各自事务执行；缺失迁移步骤时读取旧 Life 行明确报错，不静默当作已迁移。迁移不是原角色生活变化，不生成经历。没有通用 migration framework；未来旧原始值的人工修复可另行设计。

## 7. Location Update / Name Resolution

CharacterLifeService 对所有非空地点引用校验：实体存在且为 Place，再持久化。无效 UUID 不写入。保留 B3 partial update：省略不变，None 清空，真实变化更新时间。时间不推断当前位置，学校/住所关联也不推断当前位置。

CLI `/world` 列出名称与类型；`/life location <canonical name>` 必须精确唯一匹配，重名拒绝并提示 ambiguous；`/life location-id <uuid>` 为开发调试入口；`/life location none` 清空。家当前 canonical name 为“住宅区的家”，因此用 `/life location 住宅区的家`，不做自由字符串别名猜测。这些操作不调用 LLM。

## 8. Prompt Projection / World Truth vs Character Knowledge

LifeService 解析当前 home/school/primary area/location 的 canonical_name，生成独立 ProjectedLifeContext；Conversation 将它交给 PromptBuilder。仍使用 `【当前生活上下文】`，只显示已知值。不输出 World UUID、parent、description、created_at/updated_at 或 DB metadata；不列出全部世界。

World Truth ≠ Character Knowledge。World DB 中存在实体或描述，不说明角色知道它的全部信息。当前只展示 Life 相关名称，不自动注入 description；不推断课程、开放状态、天气或行程。原有对话规则及中性 few-shot 未修改。

## 9. World vs State / World vs Memory

Location ≠ Activity。到校引用更新不能自动改为“上课”，也不改 energy、attention、mood、social_engagement。World 初始化和 Life 位置更新都不自动形成 Memory，不等于“记得自己去了学校”。Clock 推进 12 小时不移动位置。B2 State reconciliation 和 A3 Memory 流程不改变。

## 10. Event Integration

未增加 location_changed：当前 A4 Event payload/result 仍专用于 State。使用显式 LifeService API 足够，不让 CharacterStateTransitionService 处理地点。未来有明确需求时事件可委托 LifeService，但本阶段不扩展 Event 架构。

## 11. Known Limitations / Future Work

无 dynamic world state、NPC、组织、天气、营业时间、地图、坐标、距离、邻接、路线、自动移动、scheduler、离线世界模拟、世界事件历史或 Character perception。没有 place_type ontology、GIS、graph database、ECS、房间/课程模拟、LLM world generation、关系演化或管理 UI。未来仅在明确需求下扩展引用、感知和事件，不由本版自动补全世界。

## 12. Verification

`python scripts/run_world_state_smoke.py` 离线使用临时 SQLite 与 FixedClock，检查 seed、parent、Life UUID 绑定、自然语言 Prompt、显式位置更新、+12h、不变的 State/Memory，以及重启持久化与清理。pytest 不访问 Hugging Face 或真实 API，并覆盖精确/未知/重名迁移、重复初始化、seed 补充和引用拒绝。
