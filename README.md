# Evolving-Companion-Agent

一个面向长期陪伴的自主 Agent 项目，长期探索持续身份、长期记忆、人格演化、关系发展、虚拟生活、世界感知与未来实体迁移。

## 当前状态

- 开发阶段：**A3.5 — Memory Conflict / Supersede / Consolidation v0.1**。
- Current Character：`SI-001`（开发代号）。
- Working Name：**玲**，目前仅为工作名，尚未正式确认为 Personal Name。
- Identity Stage：`Pre-Identity`；Birthday 尚未确定。

当前提供通过 DeepSeek API 进行多轮 CLI 对话的最小原型。SI-001 的 Seed Character Data 由 `data/characters/si_001.yaml` 唯一维护，经 Pydantic 校验和 Character Projection 后传给 PromptBuilder。
A3.1 将原始对话立即归档到默认的 `runtime/si_001.db`，并建立 SQLite Memory Store 与 Evidence Chain 的显式存储 API。当前会话的 Prompt History 仍只保存在内存中，退出后清空。Archive 是系统原始记录，不自动成为 Character Memory 或 Character Knowledge。
A3.2a 提供对给定 Conversation Chunk 执行一次结构化 Memory Extraction、证据校验及保存 Gate 的接口。A3.2b 建立本地向量检索 API；`memories` 与 `memory_evidence` 是权威 Memory 数据，`memory_embeddings` 是可删除并重建的派生索引。
A3.2c 的 rerank runner 是离线实验工具，仅对生产 Retriever 给出的 semantic Top-10 候选进行轻量实验重排，不改变 Production MemoryRetriever。
A3.2d 增加本地 CrossEncoder relevance reranker 实验，以原始 logit 与 sigmoid score 观察排序及相关/无关分布；仍不接入生产对话链路，也不设置 relevance threshold。

A3.2e 为 experimental relevance gate benchmark：使用独立合成 fixture 比较当前消息与最近上下文的最高相关性分数分布。production 仍未启用 relevance gate、未注入长期 memory，threshold 尚未冻结。人工运行 `python scripts/run_memory_relevance_gate_benchmark.py`（CPU，临时 SQLite；首次运行可能下载模型），可通过 `--context-messages 3` 至 `6` 调整上下文消息数，默认 5；测试不加载模型。

A3.2f 在同一实验 fixture 上评估确定性的 Memory Need 前置规则与 Selective Context Policy，并与 current-only / recent-context 基线比较。Semantic relevance 不等于 Memory need；直接拼接最近上下文可能造成 context pollution。fixture 结果不代表规则已证明可泛化。
A3.3 将 Memory Need → Selective Context → semantic Top-10 → 本地 Cross-Encoder → Top-3 candidate memory 注入 Conversation/Prompt。没有全局 reranker threshold，也不做 salience/recency rerank；Prompt 将召回项表达为可忽略的候选上下文，不是系统事实。规则仍可能无法覆盖所有自然语言。人工运行 `python scripts/run_memory_injection_smoke.py` 可用临时 SQLite 和真实本地检索/重排模型检查注入；默认使用 fake LLM，传入 `--real-llm` 才调用 DeepSeek。
A3.4 在主回复成功并归档后，对当前 user/assistant 消息对执行一次高精度 Memory Extraction；提取出的有效 `save` 候选通过现有 evidence 校验和 normalized active-content dedup 后写入 SQLite。Extraction 失败不影响主回复；`reject` / `uncertain` 不落库。人工运行 `python scripts/run_memory_formation_smoke.py` 使用临时 SQLite；默认是 fake 主 LLM 与 deterministic extractor，决策仅用于流程演示；传入 `--real-llm` 才调用 DeepSeek。
A3.5 对新保存的记忆检索最多 5 条相关 active 旧记忆，由 judge 在 `keep_both` / `supersede_old` / `uncertain` 中判断。只有明确当前状态替代才 supersede；旧记忆不删除、不改内容或 evidence，仍保留在数据库并从生产 active recall 排除。当前不做 memory merge 或 summary。人工运行 `python scripts/run_memory_consolidation_smoke.py` 使用临时 SQLite；默认 fake 回复与 deterministic extraction/judge；传入 `--real-llm` 才使用 DeepSeek 进行 extraction 和判断（本地检索/重排仍使用 CPU 模型）。Consolidation 失败不影响回复或新 memory。

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
