# Evolving-Companion-Agent

一个面向长期陪伴的自主 Agent 项目，长期探索持续身份、长期记忆、人格演化、关系发展、虚拟生活、世界感知与未来实体迁移。

## 当前状态

- 开发阶段：**A0 — Project Organization**。
- Current Character：`SI-001`（开发代号）。
- Working Name：**玲**，目前仅为工作名，尚未正式确认为 Personal Name。
- Identity Stage：`Pre-Identity`；Birthday 尚未确定。

当前仅具备最小 Python 工程底座：可安装的包、import 测试、Ruff 检查和 GitHub Actions CI。
尚未实现可用的 Character、对话运行时、记忆、自主行为或世界模拟，也没有应用启动入口。
下一阶段为 A1 — Minimal Character Conversation。

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
A0 无运行时依赖；开发依赖仅为 pytest 和 Ruff。CI 在 Python 3.12 上执行相同的安装与检查步骤。

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
