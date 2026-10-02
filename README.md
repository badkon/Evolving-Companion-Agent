# Evolving-Companion-Agent

一个面向长期陪伴的自主 Agent 项目，长期探索持续身份、长期记忆、人格演化、关系发展、虚拟生活、世界感知与未来实体迁移。

## 当前状态

- 开发阶段：**A7.2 — First-run Setup v0.1**（Windows 离线/headless 验证；Linux/systemd 与真实 QQ 部署尚未验证）。
- Current Character：`SI-001`（开发代号）。
- Working Name：**玲**，目前仅为工作名，尚未正式确认为 Personal Name。
- Identity Stage：`Pre-Identity`；Birthday 尚未确定。

当前提供通过 DeepSeek API 进行多轮 CLI 对话的最小原型。SI-001 的 Seed Character Data 由 `data/characters/si_001.yaml` 唯一维护，经 Pydantic 校验和 Character Projection 后传给 PromptBuilder。
A3 建立了长期记忆 v1 的 archive/evidence 存储、按需 recall 与 Top-3 候选注入、成功对话后的 memory formation，以及保守的 conflict/supersede 流程。Archive 与 Memory 分离；旧记忆保留为历史，生产 recall 仅使用 active memory。冻结架构、实验结论和限制见 [A3 Long-term Memory v1](docs/a3_long_term_memory_v1.md)。

A4 v0.1 增加基于 `identity.internal_id` UUID 的单行当前 Character State 持久化、显式部分更新和独立 Prompt 状态区块。A4.1 增加确定性显式事件转变和 elapsed-time 规则；Conversation 在处理本轮前应用时间规则，不从对话语义推断 State。设计与限制见 [A4 Character State v0.1 / A4.1 State Transition](docs/a4_character_state_v01.md)。

A4.2 增加内存中的显式 `CharacterEvent` 与 `CharacterEventService`，映射到现有 State Transition；事件本身不持久化，状态仍使用 `identity.internal_id` 写入原表。设计与限制见 [A4.2 Explicit Character Events](docs/a4_2_character_events_v01.md)。

B1 增加注入式 UTC Clock、显式 Character 时区、时间快照、最后成功对话时间持久化、离线时长，以及 Prompt 时间投影；State、Event、Conversation 时间路径统一使用 Clock。当前 Seed 的 `Asia/Shanghai` 是开发期 Character 配置，不是用户位置推断。详见 [B1 Time Model](docs/b1_time_model_v01.md)。

B2 按四个独立字段时间锚点将短期 State 归一到工程中性 baseline；无变化检查不刷新时间，elapsed 不清除活动，也不表示离线期间发生过任何经历。旧 State 表自动回填锚点。详见 [B2 Temporal State Reconciliation](docs/b2_temporal_state_reconciliation_v01.md)。

当前尚未实现 memory merge、summary、自主行为或世界模拟。

B3 增加独立持久化的 Personal Life Context，按 Internal UUID 绑定。已有生活设定仅用于首次初始化，当前位置未定义时保持空值；位置和身份只允许显式更新，不由时间或 LLM 自动推断，也不自动形成 Memory。详见 [B3 Personal Life Scaffold](docs/b3_personal_life_scaffold_v01.md)。

B4 建立最小静态 World Place 层，固定 UUID seed 初始化到 SQLite；Life 的地点关联改为 World UUID，并兼容迁移 B3 旧字符串。Prompt 只解析相关地点名称，不自动获得 World description 或亲历记忆。详见 [B4 World State](docs/b4_world_state_v01.md)。未实现动态世界模拟。

B5 增加 [Lightweight NPC Registry](docs/b5_lightweight_npc_registry_v01.md)：独立 SQLite 静态记录、稳定 UUID、显式 Place 关联和 active 标记。正式 NPC 初始为 0；只有 SI-001 是完整 Character，NPC 无 LLM、Memory、后台任务，资料不直接进入 Prompt。离线检查：`python scripts/run_npc_registry_smoke.py`。

B6 增加 [Observation Layer](docs/b6_observation_layer_v01.md)：CLI / QQ 每轮在同一只读事务中捕获 Life 与当前 Place 的 active NPC 引用，独立 Prompt 区块仅提供地点和匿名人物存在信息。最多 20 条、无缓存/持久化；未知位置不猜测，查询失败省略本轮组合上下文。Observation ≠ Knowledge ≠ Experience ≠ Memory；无识别、Action 或 Scheduler。离线检查：`python scripts/run_observation_smoke.py`，仅使用临时 DB 和 synthetic NPC。

B7 增加 [Action Intent / Resolver](docs/b7_action_resolver_v01.md)：仅受信任 developer/test 显式提交 typed move_to；位置更新与 terminal receipt 原子提交，action_id 持久化幂等，旧动作重放不改变当前位置。Life 普通部分更新也改为事务内读取最新行并仅 patch 显式字段，避免覆盖并发移动。没有聊天/LLM 动作解析、自主决策、NPC 行动或 Scheduler；不改 State / Memory。离线检查：`python scripts/run_world_action_smoke.py`，仅临时 DB。

B8 增加 [Lazy World Temporal Context](docs/b8_lazy_world_time_v01.md)：按访问由共享 Clock 和已有 Character 时区计算当前时段，经 B6 环境区块简短投影；不持久化、无 Scheduler，不改变位置、NPC、State 或 Memory。Conversation 一轮只采样一次时间，成功交流记账复用本轮时间。离线检查：`python scripts/run_world_time_smoke.py`，包含 B8 单阶段与 B6+B7+B8 组合 smoke，仅使用临时 DB。

## 开发环境

Linux 服务器完成 A7 安装并将 `/opt/si/app/.venv/bin` 加入 PATH 后，运行 `si setup` 安全配置 DeepSeek/SiliconFlow keys、API-only Memory 与 QQ/None；密钥输入 masked，不需要手工 export API key。Review 确认后原子保存配置、离线检查，再可选启动服务；之后运行 `si` 日常运维。详见 [First-run Setup](docs/a7_2_first_run_setup_v01.md)。离线 smoke：`python scripts/run_setup_smoke.py`。

A7.1 为已部署 Linux 实例提供 SI Manager：将 `/opt/si/app/.venv/bin` 加入 PATH 后运行 `si`（`si --help` 查看帮助），进入 Overview、Service、Deployment Check、Health、只读 Configuration、Backup / Restore、Logs。配置不显示密钥；恢复需要确认及服务停止。操作与权限限制见 [SI Manager TUI](docs/a7_1_si_manager_tui_v01.md)。离线 smoke：`python scripts/run_si_manager_smoke.py`。

A7 提供 Linux `/opt/si` 部署资产、专用 si 用户、uv 锁定的 API-only 环境、systemd、离线 deploy_check/health 和 SQLite-safe backup/restore。config/runtime/backups 与 app checkout 分离；`SI_RUNTIME_DB` 支持外置数据库，本地默认不变。首次安装不自动启动或覆盖配置。完整步骤、持久化合同及限制见 [A7 Deployment Foundation](docs/a7_deployment_foundation_v01.md)。

A5 已建立离线 QQ 私聊 Adapter Core：安全过滤后仅调用既有 Conversation 的 `send(text)`，角色回复原样返回；不登录 QQ、不启动 SnowLuma、不建立网络连接。设计与运行边界见 [A5 QQ Private Alpha](docs/a5_qq_private_alpha_v01.md)。完全离线检查：`python scripts/run_qq_adapter_smoke.py`。

A5.1 新增真实 forward OneBot WebSocket client，仍不负责启动 / 登录 SnowLuma。配置 `SI_QQ_BOT_USER_ID`、逗号分隔的 `SI_QQ_ALLOWED_USER_IDS`、可选 `SI_ONEBOT_WS_URL` / `SI_ONEBOT_ACCESS_TOKEN` 后，使用 `python -m evolving_companion.qq_transport` 启动（还需既有 DeepSeek key）。仅本地 Fake Server 检查：`python scripts/run_onebot_transport_smoke.py`，不需要任何 key、QQ 或 SnowLuma。见 [A5.1 Transport](docs/a5_1_onebot_transport_v01.md)。

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
A1 使用 OpenAI Python SDK 作为 DeepSeek OpenAI-compatible API 的通信客户端；A2 使用 Pydantic 校验 Seed Data、PyYAML 读取 YAML；Memory Retrieval 核心使用 NumPy 和 HTTPX。A6 将本地 BGE 的 `sentence-transformers` 移至可选 `local-memory` 依赖；本地对话/已有 BGE benchmark 请安装 `python -m pip install -e ".[dev,local-memory]"`。API-only 和 CI 可继续使用 `.[dev]`，不强制安装 torch/transformers。

A6 默认仍为 local/local；CLI 和 QQ 入口支持显式 API provider 配置，沿用 `.env.local` 且不覆盖系统环境变量。配置、HTTP 格式和限制见 [A6 Memory Providers](docs/a6_memory_retrieval_providers_v01.md)。完全离线验证：`python scripts/run_memory_provider_smoke.py`（临时 DB、fake API，无需 key 或模型）。

首次运行真实 LLM 前，在仓库根目录复制 `.env.example` 为 `.env.local`，并填写 `DEEPSEEK_API_KEY`。`.env.local` 已被 Git 忽略，不应提交。已有的操作系统环境变量优先，不会被本地文件覆盖。

随后可直接启动 CLI：

```bash
python -m evolving_companion.cli
```

输入 `/exit` 退出。API Key 不应写入仓库或打印到终端日志。
CLI 中 `/state` 及其参数是本地开发调试命令，不会调用 LLM。
离线检查 A4.1 transition：`python scripts/run_character_state_transition_smoke.py`。可选 `--real-llm` 仅用于观察 Prompt 表达，不属于规则验证。
离线检查 A4.2 显式事件：`python scripts/run_character_event_smoke.py`。
离线检查 B1 时间模型：`python scripts/run_time_model_smoke.py`。
离线检查 B2 状态归一：`python scripts/run_temporal_state_reconciliation_smoke.py`。
CLI 中 `/life` 显示生活上下文；`/life location 学校`、`/life role 学生` 显式更新，值为 `none` 时清空对应字段。这些命令不会调用 LLM。
离线检查 B3 生活支架：`python scripts/run_personal_life_smoke.py`。
B4 中 `/world` 列出地点；`/life location <canonical name>` 要求精确唯一匹配，或使用 `/life location-id <uuid>`。家的名称为“住宅区的家”，不按模糊别名匹配。
离线检查 B4 地点与引用：`python scripts/run_world_state_smoke.py`。

## 设计文档

[Character Structural Design](docs/character/character-design.md) 是当前角色结构性设计的版本控制 **Source of Truth**。
[SI World Design v0.1](docs/world_design_v01.md) 是当前世界空间规划的人类可读 source of truth；它与只包含最小运行地点的 B4 World Seed 分开维护。
[DOCX 版本](docs/character/SI-001_Character_Design_Structural_v0.2.docx) 作为导出和展示产物（export / presentation artifact）。
设计文档描述长期蓝图，不代表当前已实现的功能。

- [项目愿景](docs/vision.md)
- [架构原则与工程状态](docs/architecture.md)
- [A3–B4 Milestone Architecture Review](docs/milestone_a3_b4_architecture_review.md)
- [Character Constitution](docs/character-constitution.md)
- [Character Identity](docs/identity.md)
- [System Governance](docs/system-governance.md)
- [开发规范](CONTRIBUTING.md)
- [编码 Agent 规则](AGENTS.md)

## 数据边界

Character Runtime Data 不得进入 Git，包括私人对话、记忆、角色状态和数据库运行文件。
API Key 等秘密信息同样不得提交；现有 `.gitignore` 覆盖本地环境、运行数据、日志、缓存和构建产物。
