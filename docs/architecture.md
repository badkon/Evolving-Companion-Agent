# Architecture

> Evolving-Companion-Agent 架构设计

本文档记录已经确定的架构原则与当前技术方向。

尚未确定的技术选择应明确标记，不将候选方案描述为最终决定。

## 当前工程状态 — A1

项目使用 Python >= 3.12、`src` layout 和 `evolving_companion` 包，通过 setuptools 与标准 pip 安装。
A1 提供 CLI、多轮内存会话和一个精简 CharacterProfile；LLM 层通过 OpenAI Python SDK 调用 DeepSeek 的 OpenAI-compatible API。
API Key 从 `DEEPSEEK_API_KEY` 环境变量读取。运行时依赖为 openai，开发依赖为 pytest 和 Ruff。

会话历史只在进程内存中保存。当前没有长期记忆、Character Data 持久化、数据库、World/NPC 模拟或自主行为。
下文的 Character Store、SQLite 和多设备服务仍属于架构方向，尚未实现。

## 1. 核心分层

项目遵循以下基本原则：

> Character ≠ LLM ≠ Client ≠ Device ≠ Body

Character 的身份与长期状态不应绑定于特定模型、操作系统、客户端或物理设备。

---

## 2. 基本结构

当前总体方向：

Client
↓
Character Core
↓
Character Store

其中：

- Client 负责人机交互；
- Character Core 负责认知、记忆、人格、关系、兴趣、世界等核心逻辑；
- Character Store 负责持久化 Character State。

各层之间应保持明确边界。

---

## 3. Character State

长期采用单一权威 Character State。

不同设备作为同一 Character 的访问入口，不分别维护独立人格。

当前阶段允许状态存储在本地。

未来出现 Windows + macOS 等多设备需求后，可迁移为：

Windows / macOS / Other Clients
↓
Character Server
↓
Character Store

当前不考虑离线多主同步。

---

## 4. 数据与代码分离

源代码与 Character Runtime Data 必须分离。

Character Runtime Data 包括但不限于：

- 对话；
- 记忆；
- 人格状态；
- 情感状态；
- 关系状态；
- 用户模型；
- 世界状态；
- 经历记录。

Runtime Data 不进入 Git。

---

## 5. 存储

当前计划优先使用 SQLite 作为早期 Character Store。

上层模块不应直接依赖具体数据库实现，应通过统一的数据访问边界访问 Character State。

这样未来可以在不重写核心逻辑的情况下迁移到 Character Server 或其他存储方案。

---

## 6. 技术方向

当前倾向：

- Character Core：Python；
- Character Store：SQLite；
- Client：待聊天底座选型后确定；
- Desktop：未来考虑 TypeScript 与 Tauri 等方案；
- Server：未来优先考虑 Linux。

以上部分技术选型仍可能随着原型验证而调整。

---

## 7. 当前工程原则

当前阶段：

1. 优先复用成熟的基础聊天能力；
2. 不重复实现与 Character 核心无关的成熟基础设施；
3. 不提前实现尚未出现实际需求的复杂系统；
4. 为未来多设备与服务器迁移保留清晰边界；
5. Character 核心状态应尽可能与具体平台无关。
