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