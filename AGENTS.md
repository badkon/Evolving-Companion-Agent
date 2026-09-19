# Agent Instructions

本文件定义 AI 编码 Agent 在 Evolving-Companion-Agent 仓库中的基本工作规则。

## Before Coding

修改代码前，应根据任务范围阅读相关文档：

- `README.md`：项目概览；
- `CONTRIBUTING.md`：开发规范；
- `docs/vision.md`：项目目标与长期方向；
- `docs/architecture.md`：当前架构；
- `docs/character-constitution.md`：Character 身份、人格与演化原则；
- `docs/system-governance.md`：系统与开发者权限。

涉及 Character 行为、人格、记忆、关系、兴趣、自主性或长期状态时，必须阅读 Character Constitution。

涉及权限、隐私、数据控制、备份、迁移或开发者操作时，必须阅读 System Governance。

## Design Authority

现有设计文档是当前项目实现的设计依据。

如果任务要求与现有设计文档发生冲突：

1. 不得静默修改设计原则；
2. 明确指出冲突；
3. 优先请求确认；
4. 不为了方便实现而自行降低或改变产品目标。

## Scope

只实现当前任务明确要求的内容。

除非确有必要：

- 不进行无关重构；
- 不提前实现未来功能；
- 不引入不必要的依赖；
- 不创建没有实际用途的模块；
- 不擅自扩大任务范围。

## Character Data

不得将真实 Character Runtime Data、私人对话、记忆数据库、用户数据、API Key 或其他秘密信息提交到 Git。

## Documentation

如果实现改变了已经记录的架构事实，应同步更新相关技术文档。

不得自行修改 Character Constitution 或 System Governance 中的高层原则来适配代码实现。

## Reuse Before Reinvention / 优先复用成熟方案

在实现新的重要模块前，应先调查相关的开源项目、成熟库和相似系统，判断是否存在可直接复用、改造或借鉴的实现。

项目不以“全部自主实现”为目标。通用工程能力应优先采用经过验证的成熟方案，自主开发应主要集中于 Character Identity、Mind、Personality、Relationship、World 等具有本项目特殊设计目标的部分。

开发流程原则上遵循：

需求定义 → 开源方案调查 → 复用评估 → 架构决策 → 实现

对候选方案至少评估：

- 与当前需求的匹配程度；
- License 与代码复用条件；
- 项目维护状态与成熟度；
- 依赖规模与引入成本；
- 与现有架构的耦合程度；
- 数据、隐私与运行边界；
- 后续替换和迁移难度。

评估后的处理方式分为：

- **Direct Reuse**：直接复用；
- **Adapted Reuse**：修改后复用；
- **Design Reference**：仅参考设计；
- **Custom Implementation**：自行实现。

发现已有开源实现并不意味着必须引入。

避免为了较小功能引入体量过大、依赖复杂或会反向控制项目架构的完整框架。必要时只提取其中与当前需求相关的模块、算法或设计思想。

已有参考项目不具有默认优先权。即使项目中已经使用或参考某个开源项目，在开发新的重要模块时，也应根据当前需求重新调查和比较其他可用方案。

Cyrene-Agent 目前是重要参考实现之一，但不是 Evolving-Companion-Agent 的母项目或默认架构来源。

任何外部代码的实际引入都必须遵守其许可证要求，并保留必要的版权、许可证和来源信息。