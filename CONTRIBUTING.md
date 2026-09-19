# Development Guidelines

> Evolving-Companion-Agent 开发规范

本文档记录项目当前确定的基础开发约定。

规则会随着项目发展逐步补充，不提前建立尚未产生实际需求的复杂规范。

## 1. Language

### 文件与代码

以下内容统一使用英文：

- 文件名
- 目录名
- 代码变量名
- 函数名
- 类名
- 接口名
- 数据库字段名
- 配置项名称

避免在程序路径和代码标识符中使用中文，以降低 Windows、macOS、Linux 之间的兼容风险。

### 文档

项目文档正文默认使用中文。

Markdown 文件名使用英文，例如：

- `product-definition.md`
- `character-constitution.md`
- `system-governance.md`
- `architecture.md`

### Git 提交

Git Commit Message 默认使用中文，要求简洁说明本次提交的实际变化。

例如：

- `配置项目基础忽略规则与开发规范`
- `完善角色存在核心定义`
- `实现长期记忆存储原型`
- `修复记忆检索重复问题`

不强制使用 Conventional Commits。

---

## 2. Repository Structure

项目采用按需增长原则。

不提前创建尚未使用的目录和模块。

只有当对应功能开始设计或实现时，才创建相关目录，例如：

- `docs/`
- `src/`
- `tests/`
- `scripts/`

避免为了预设架构而维护大量空目录。

---

## 3. Character Data

程序代码与角色运行数据必须分离。

以下数据不得提交到 Git：

- 对话记录
- 长期记忆
- 人格状态
- 情感状态
- 关系状态
- 用户模型
- 世界运行状态
- 私人角色数据
- 数据库运行文件

这些数据属于 Character Runtime Data，而不是源代码。

---

## 4. Secrets

任何密钥和私人凭据不得提交到 Git，包括：

- LLM API Key
- Token
- Password
- Private Endpoint
- 其他身份验证信息

实际密钥通过环境变量或本地配置提供。

如果未来需要提供配置示例，应使用 `.env.example` 等不包含真实密钥的模板文件。

---

## 5. Cross-platform Compatibility

项目从设计阶段考虑：

- Windows
- macOS
- Linux

核心逻辑不得无必要地依赖特定操作系统。

避免在核心代码中写死：

- Windows 盘符
- 用户目录
- 绝对路径
- 特定系统命令

平台相关能力应在需要时通过独立适配层实现。

---

## 6. Character State

长期目标采用单一权威 Character State。

不同设备应作为同一 Character 的访问入口，而不是分别维护独立的人格、记忆和关系副本。

当前阶段允许 Character State 存储于本地；未来可迁移至统一的 Character Server。

角色状态的存储方式不应与上层人格、记忆和认知逻辑强耦合。

---

## 7. Development Principle

当前阶段优先：

1. 验证核心设计；
2. 保持结构简单；
3. 保证状态与数据可迁移；
4. 在出现实际需求后再增加复杂度。

不为了未来可能出现的需求提前实现复杂系统。