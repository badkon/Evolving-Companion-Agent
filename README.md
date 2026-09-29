# Evolving-Companion-Agent

一个面向长期陪伴的自主 Agent 项目，长期探索持续身份、长期记忆、人格演化、关系发展、虚拟生活、世界感知与未来实体迁移。

## 当前状态

- 开发阶段：**A4.1 — State Transition v0.1 — Complete**。
- Current Character：`SI-001`（开发代号）。
- Working Name：**玲**，目前仅为工作名，尚未正式确认为 Personal Name。
- Identity Stage：`Pre-Identity`；Birthday 尚未确定。

当前提供通过 DeepSeek API 进行多轮 CLI 对话的最小原型。SI-001 的 Seed Character Data 由 `data/characters/si_001.yaml` 唯一维护，经 Pydantic 校验和 Character Projection 后传给 PromptBuilder。
A3 建立了长期记忆 v1 的 archive/evidence 存储、按需 recall 与 Top-3 候选注入、成功对话后的 memory formation，以及保守的 conflict/supersede 流程。Archive 与 Memory 分离；旧记忆保留为历史，生产 recall 仅使用 active memory。冻结架构、实验结论和限制见 [A3 Long-term Memory v1](docs/a3_long_term_memory_v1.md)。

A4 v0.1 增加基于 `identity.internal_id` UUID 的单行当前 Character State 持久化、显式部分更新和独立 Prompt 状态区块。A4.1 增加确定性显式事件转变和 elapsed-time 规则；Conversation 在处理本轮前应用时间规则，不从对话语义推断 State。设计与限制见 [A4 Character State v0.1 / A4.1 State Transition](docs/a4_character_state_v01.md)。

当前尚未实现 memory merge、summary、自主行为或世界模拟。

## 开发环境

需要 Python **3.12 或更高版本**。在仓库根目录创建虚拟环境：

```bash
python --version
python -m venv .venv
```

激活环境，按所用平台选择：

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS / Linux：

```bash
source .venv/bin/activate
```

在已激活的环境中安装项目并运行检查：

```bash
python -m pip install -e ".[dev]"
pytest
ruff check .
ruff format --check .
```

如 PowerShell 阻止激活脚本，可直接使用虚拟环境中的命令：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
```

Python import 包名为 `evolving_companion`，源码位于 `src/evolving_companion/`。
A1 使用 OpenAI Python SDK 作为 DeepSeek OpenAI-compatible API 的通信客户端；A2 使用 Pydantic 校验 Seed Data、PyYAML 读取 YAML；A3.2b 使用 sentence-transformers 加载本地 BGE 模型，并使用 NumPy 处理向量。运行时依赖为 `openai`、`numpy`、`pydantic`、`PyYAML`、`python-dotenv` 和 `sentence-transformers`，开发依赖为 pytest 和 Ruff。CI 在 Python 3.12 上执行相同的安装与检查步骤。

首次运行真实 LLM 前，在仓库根目录复制 `.env.example` 为 `.env.local`，并填写 `DEEPSEEK_API_KEY`。`.env.local` 已被 Git 忽略，不应提交。已有的操作系统环境变量优先，不会被本地文件覆盖。

随后可直接启动 CLI：

```bash
python -m evolving_companion.cli
```

输入 `/exit` 退出。API Key 不应写入仓库或打印到终端日志。
CLI 中 `/state` 及其参数是本地开发调试命令，不会调用 LLM。
离线检查 A4.1 transition：`python scripts/run_character_state_transition_smoke.py`。可选 `--real-llm` 仅用于观察 Prompt 表达，不属于规则验证。

## 设计文档

[Character Structural Design](docs/character/character-design.md) 是当前角色结构性设计的版本控制 **Source of Truth**。
[DOCX 版本](docs/character/SI-001_Character_Design_Structural_v0.2.docx) 作为导出和展示产物（export / presentation artifact）。
设计文档描述长期蓝图，不代表当前已实现的功能。

- [项目愿景](docs/vision.md)
- [架构原则与工程状态](docs/architecture.md)
- [Character Constitution](docs/character-constitution.md)
- [Character Identity](docs/identity.md)
- [System Governance](docs/system-governance.md)
- [开发规范](CONTRIBUTING.md)
- [编码 Agent 规则](AGENTS.md)

## 数据边界

Character Runtime Data 不得进入 Git，包括私人对话、记忆、角色状态和数据库运行文件。
API Key 等秘密信息同样不得提交；现有 `.gitignore` 覆盖本地环境、运行数据、日志、缓存和构建产物。
