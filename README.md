# Evolving-Companion-Agent

一个面向长期陪伴的自主 Agent 项目，长期探索持续身份、长期记忆、人格演化、关系发展、虚拟生活、世界感知与未来实体迁移。

正式 CLI / QQ 默认使用 [Natural Conversation Pipeline v1](docs/natural_conversation_pipeline_v1.md)：
结构化回复规划、表达意图、24 条表达习惯的上下文选择、基础/临时风格与独立 Replyer。
`SI_REPLY_PIPELINE=legacy` 可切回原路径，未配置时为 `natural`。每轮回复侧增加一次有预算的规划调用；
Memory/Character/World 边界不变。用户已报告原 QQ 链路打通，新管线的真实 QQ 自然度仍待验收。

[Persona Activation & Character Distinctiveness v1](docs/persona_activation_v1.md)
从现有 Seed 预筛选特征，由同一次 Planner 选择 0–3 条交给 Replyer；保留简单回复、稳定立场和
Grounding，不新增人格状态或第三次主回复调用。合成测试不代表真实 QQ 自然度已验收。

可通过 `SI_REPLY_PIPELINE=natural_simplified` 使用
[Simplified Conversation Pipeline v1](docs/simplified_conversation_pipeline_v1.md)：
Relevant Context → 四字段 Tiny Planner → Replyer，旁路表达习惯与临时风格。
默认 `natural` 保持完整管线，`natural_full` 是其别名，`legacy` 兼容原单次回复路径。
离线 A/B：`python scripts/run_simplified_conversation_smoke.py --pipeline both`。
简化模式另提供 Mini Life Context v0.1：按 Seed、时区和固定时间块计算 now/next/today，
仅在相关轮次注入紧凑摘要，不写生活历史或数据库；离线检查：`python scripts/run_mini_life_smoke.py`。
53 个离线合成场景覆盖生活相关/无关、关系/affect、Memory 和 Grounding；真实 QQ 自然度仍待 A/B 验收。

## 快速开始（Ubuntu 源码部署）

[Vision Input & Sticker Understanding v0.1](docs/vision_input_v0_1.md) 为 QQ 的
`natural_simplified` 增加外部 API 视觉观察：普通图片、截图和静态表情图进入原 Tiny Planner / Replyer。
默认关闭，需在现有 env 配置 Vision provider/model/key；最多三图、每图 8 MiB，仅内存处理。
不保存原图、不自动形成图片 Memory、不发送表情包；完整 `natural` 保持安全降级。
离线端到端检查：`python scripts/run_vision_input_smoke.py`。真实 QQ / Vision 模型仍待验收。

默认自然回复已接入 [Affective & Relationship State v1](docs/affective_relationship_state_v1.md)：
同一次 Planner 完成 Appraisal，独立持久化 Emotion / Mood / 按用户关系；新状态影响表达。
主要用户熟人关系只初始化一次、非恋爱；没有第三次情绪 LLM 或主动消息。新 QQ 行为仍待验收。

先 clone 本仓库；在已安装 Python 3.12 和 `python3.12-venv` 的环境，以同一个普通用户执行：

```bash
cd Evolving-Companion-Agent
bash install.sh
export PATH="$HOME/.local/bin:$PATH"
si
```

选择 **配置** → 按提示 SSH 转发并打开 **SI Console** → 在模型与服务 / 角色配置 / 聊天管理页配置 → 测试连接 → 保存 → 检查配置 → **运行与日志 → 启动**（也可从管理器启动）。
不需要手动建 venv 或编辑配置文件。用户已报告上一版 Linux 安装、配置、API 测试与 Core 控制通过；本轮 Console 仍需浏览器 / Linux 验收，真实 QQ 私聊尚未最终验收。详见 [Console 指南](docs/si_console_v01.md)。

## 当前状态

- 已加入 Docker Compose Server Runtime Foundation（API-only、宿主机持久化）；通用 Runtime Configuration、Setup、Health 和 Backup / Restore 保留。
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

推荐源码部署见 [Deployment UX v0.1](docs/deployment_ux_v01.md)：Python 3.12+ 下运行 `bash install.sh` → `si` → 配置 → SI Console → 检查配置 → 启动。安装器只准备当前 checkout/venv/用户 launcher，不改账户或系统服务。Console 新界面与真实 QQ 私聊仍需验收。另保留 [Docker Server Deployment v1](docs/docker_server_deployment_v1.md)，不提供 Docker 自动安装、自动更新或已发布镜像。

正式 server 支持 `SI_CHAT_TRANSPORT=none`：完成共享 Core 初始化后无聊天常驻，不要求 QQ 配置，SIGINT/Ctrl+C 或 SIGTERM 可正常退出；API keys/provider 配置仍须有效。使用 `python -m evolving_companion.server --env-file config/si.env`；qq 模式保持既有聊天行为。

安装项目后，在项目根目录运行 `si`（或 `python -m evolving_companion.manager`）进入 [SI Manager v0.1](docs/si_manager_v01.md)：启动、停止、重启、状态、日志、配置、退出。Native 控制使用当前 Python 启动独立 server；退出 Manager 关闭临时 Web，不停止 Core。不控制 Docker/systemd，不提供更新、开机自启或崩溃监控。离线 headless 检查：`python scripts/run_si_manager_smoke.py`。升级本轮依赖后需重新 `pip install -e .`。

Manager 配置启动带临时 token 的 loopback [SI Console](docs/si_console_v01.md)：首页、聊天、媒体空入口、角色、情绪与关系、只读记忆、模型、运行与日志、数据与系统。首页增加紧凑状态摘要；情绪与关系页只读展示已保存的主要对象关系、当前心情及近期情绪，手动刷新，不修改状态或调用 LLM。复用原配置 / 连接测试 / Native 控制；`si setup` 保留终端 fallback。配置仍是 Git 忽略的 `config/si.env`，不另建设置数据库。保存不自动启动 Core，不回显密钥，也不改变身份 UUID。命令与安全边界见 [Runtime Operations](docs/runtime_operations.md)。离线检查：`python scripts/run_setup_smoke.py`、`python scripts/run_web_setup_smoke.py`（真实 loopback Console smoke）。

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
