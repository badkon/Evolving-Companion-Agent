# Evolving-Companion-Agent

一个面向长期陪伴的自主 Agent 项目，长期探索持续身份、长期记忆、人格演化、关系发展、虚拟生活、世界感知与未来实体迁移。

## 当前状态

- 开发阶段：**A3.2a — 结构化 Memory Extraction 与 Memory Gate 基础**。
- Current Character：`SI-001`（开发代号）。
- Working Name：**玲**，目前仅为工作名，尚未正式确认为 Personal Name。
- Identity Stage：`Pre-Identity`；Birthday 尚未确定。

当前提供通过 DeepSeek API 进行多轮 CLI 对话的最小原型。SI-001 的 Seed Character Data 由 `data/characters/si_001.yaml` 唯一维护，经 Pydantic 校验和 Character Projection 后传给 PromptBuilder。
A3.1 将原始对话立即归档到默认的 `runtime/si_001.db`，并建立 SQLite Memory Store 与 Evidence Chain 的显式存储 API。当前会话的 Prompt History 仍只保存在内存中，退出后清空。Archive 是系统原始记录，不自动成为 Character Memory 或 Character Knowledge。
A3.2a 提供对给定 Conversation Chunk 执行一次结构化 Memory Extraction、证据校验及保存 Gate 的接口。当前不会自动处理真实会话，也尚未实现 Retrieval、Consolidation、自主行为或世界模拟。

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
A1 使用 OpenAI Python SDK 作为 DeepSeek OpenAI-compatible API 的通信客户端；A2 使用 Pydantic 校验 Seed Data、PyYAML 读取 YAML。运行时依赖为 `openai`、`pydantic` 和 `PyYAML`，开发依赖为 pytest 和 Ruff。CI 在 Python 3.12 上执行相同的安装与检查步骤。

设置 `DEEPSEEK_API_KEY` 环境变量后启动 CLI：

Windows PowerShell：

```powershell
$env:DEEPSEEK_API_KEY = "your-api-key"
python -m evolving_companion.cli
```

macOS / Linux：

```bash
export DEEPSEEK_API_KEY="your-api-key"
python -m evolving_companion.cli
```

输入 `/exit` 退出。API Key 不应写入仓库或打印到终端日志。

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
