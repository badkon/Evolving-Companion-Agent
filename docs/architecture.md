# Architecture

> Evolving-Companion-Agent 架构设计

本文档记录已经确定的架构原则与当前技术方向。

尚未确定的技术选择应明确标记，不将候选方案描述为最终决定。

## 当前工程状态 — A4 Character State v0.1 Complete

项目使用 Python >= 3.12、`src` layout 和 `evolving_companion` 包，通过 setuptools 与标准 pip 安装。
A1 提供 CLI 和多轮内存会话；LLM 层通过 OpenAI Python SDK 调用 DeepSeek 的 OpenAI-compatible API。
A2 v0.1 将初始 Seed Character Data 独立存放在 `data/characters/si_001.yaml`，数据路径为 YAML → Pydantic validation → Character Projection → PromptBuilder → LLM。
SI-001 的 Character Seed Data 已包含固定 UUID `identity.internal_id`，作为后续 Character-owned runtime records 的稳定机器身份键；`development_id = SI-001`、人类可见名称与 `continuity_generation` 各自保持独立语义。Internal UUID 不投影到普通 Character Prompt。
当前 A3 冻结基线包含 SQLite Archive / Evidence、rule-based Memory Need 与 Selective Context、active-memory semantic retrieval、CrossEncoder rerank、Top-3 candidate injection、成功回复后的 Memory Extraction，以及保守的 `keep_both` / `supersede_old` / `uncertain` consolidation。详细流程、接受/延后决策、验证证据与已知限制统一见 [A3 Long-term Memory v1](a3_long_term_memory_v1.md)，避免在此重复维护阶段细节。
LLM Adapter 只从 `DEEPSEEK_API_KEY` 环境变量读取 API Key；CLI 与真实 LLM 实验入口可在启动时从 Git 忽略的项目根目录 `.env.local` 加载该变量，且不覆盖已存在的进程环境变量。运行时依赖为 openai、numpy、pydantic、PyYAML、python-dotenv 和 sentence-transformers，开发依赖为 pytest 和 Ruff。

当前 A4 v0.1 已增加本地 SQLite 当前 Character State，使用 `identity.internal_id` UUID 作为 State 主键；Conversation 只读并独立投影该状态。具体范围见 [A4 Character State v0.1](a4_character_state_v01.md)。当前没有 memory merge/summary；也没有 World/NPC 模拟或自主行为。完整 Character Store 和多设备服务仍属于架构方向，尚未实现。

## 1. 核心分层

项目遵循以下基本原则：

> Character ≠ LLM ≠ Client ≠ Device ≠ Body

Character 的身份与长期状态不应绑定于特定模型、操作系统、客户端或物理设备。

---

## 2. 基本结构

当前总体方向：

Authoritative Character Data + Current Character State + Relevant Long-term Memory
↓
PromptBuilder / Character Core
↓
LLM

Character State 按 `identity.internal_id` UUID 绑定；它与 `development_id`、Personality、Memory 和 World State 分离。

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

当前使用 SQLite 保存对话原始记录、显式创建的 Memory，以及 A4 v0.1 的单行当前 Character State；完整 Character Store 尚未实现。

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
