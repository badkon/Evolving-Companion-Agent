# Runtime Configuration / Operations

当前保留可复用的应用能力；Linux Server 可使用 [Docker Compose 运行包装](docker_server_deployment_v1.md)，源码部署可使用 [SI Manager v0.1](si_manager_v01.md) 的 Native 控制。不提供安装器或自动更新；Native Manager 不控制 Docker。

## 能力分类

| 分类 | 能力 | 处理 |
| --- | --- | --- |
| A 通用应用能力 | API embedding/reranker、API-only inference、SQLite backup/restore 核心、既有 runtime DB 环境配置 | 保留核心行为 |
| B 旧部署外壳 | installer、账户/ownership、固定安装路径、服务管理/日志面板、shell update、锁定 bootstrap、Linux validation | 删除，无兼容分支 |
| C 解耦后保留 | Setup、配置模板/加载、Health/Check、API-only server、backup/restore CLI | 使用当前操作者、可指定路径 |
| D 当前 Native Manager | Status / Start / Stop / Restart / Logs / Configure / Exit | 仅管理自身记录并验证的 Linux server 进程，无旧部署依赖 |

Character / Conversation / Memory / State / Time / World / NPC / Observation / Action / QQ / OneBot 和 provider 核心不因本次清理改变。

## 配置与 Setup

标准 pip 安装项目后，在项目根目录运行：

```bash
si                 # Manager；q 只退出面板，不停止 Core
si setup
# 等价入口
python -m evolving_companion.setup
```

Manager Start / Restart 使用严格 runtime check，none 也是合法模式，但仍需要完整 API 配置；不采用 Setup 的历史 qq-only detection / ready 判定。Status 显示 Character 名称、短 UUID、进程状态、配置、DB、transport 与本地 health；首次 DB 未初始化时 Health 会显示 Error，Start 的配置检查仍可通过。qq 只标记 SnowLuma / OneBot，不检测在线状态。Stop 对已验证进程发 SIGINT，等待最多 15 秒；超时不强杀、不重启。Logs 仅读取最近 64 KiB / 100 行，脱敏后以纯文本展示。详细安全及恢复边界见 [Manager 文档](si_manager_v01.md)。

默认模板 `config/si.env.example` 不含真实秘密；向导默认写入 `config/si.env`，支持 `--env-file` 和 `--project-root`。配置文件及其私有备份已被 Git 忽略。密钥输入 masked、默认保留已有值；Review 不显示秘密，原子保存前保护文件并备份，未知配置行保留。POSIX 下限制文件权限，但不修改账户/ownership。Windows 下目录 ACL 由操作者管理。

加载优先级为显式进程环境变量 > 配置文件；此入口不自动加载开发 `.env.local`。`SI_RUNTIME_DB` 默认 `runtime/si_001.db`，`SI_BACKUP_DIR` 默认 `backups`；相对路径以运行时工作目录为基准，外置路径可显式配置。运行检查前需要创建数据库父目录。向导不改变 Character UUID、seed 或数据库，不初始化 runtime，也不启动程序。Transport None 可无密钥保存，仅用于暂未连接聊天的配置。

## Check / Health / API-only 入口

```bash
python -m evolving_companion.runtime_check --env-file config/si.env --offline
python -m evolving_companion.health --env-file config/si.env --offline
python -m evolving_companion.server --env-file config/si.env
```

Check 检查可写目录、SQLite 支持/完整性、已有 DB 的 Character UUID 与 seed 一致性、provider 和 transport 配置；不生成 UUID、不创建正式数据库。Health 的严格模式要求已有 DB，offline 模式可报告未初始化。`--offline` 延后 keys；默认严格模式仍需要 provider/LLM keys。qq 模式需要 QQ 配置，none 模式不读取或要求 QQ ID、allowlist、WS URL 或 token。两种检查模式均不调用远端 API，不能证明网络、账号或模型可用性。

`server` 保留 api/api 配置约束，验证通过后根据 `SI_CHAT_TRANSPORT` 分派：qq 使用现有 QQ 入口，none 进入 Core-only long-running server。两者复用 runtime.create_conversation 的同一套 Character/Memory/World 初始化。none 不初始化 QQ/OneBot，不提供 stdin 聊天、不主动调用 API，不推进世界或轮询后台任务；初始化完成后一次事件等待保持进程常驻，SIGINT/Ctrl+C 或 SIGTERM 唤醒退出，恢复 signal handlers 并经 ExitStack 清理已有 provider 资源。SQLite Store 沿用每次操作独立连接的机制。不加载本地 embedding 模型、不负责外部 OneBot 服务；开发 CLI / QQ 的既有 provider 配置和 `.env.local` 行为保持不变。Setup 可无密钥保存 None 配置，但实际 server 启动仍须完整 API 配置。

## SQLite Backup / Restore

```bash
python -m evolving_companion.backup --env-file config/si.env
python -m evolving_companion.restore /absolute/path/to/backup.db --env-file config/si.env --writers-stopped
```

备份使用标准库 SQLite backup API，包含 WAL 中已提交的数据，不以简单文件复制替代。恢复要求 backup 与目标都是已有完整 DB、Character UUID 集合一致，先生成 `pre_restore` 备份；不会创建新的 Character 或重置身份。

恢复前操作者必须停止**全部**数据库写入者。`--writers-stopped` 是显式确认而非自动检测；工具不停止/启动进程，不声称解决并发恢复或检查与写入之间的竞态。恢复失败后应检查目标及 pre_restore 备份。runtime 与 backup 是私人数据，不得进入 Git。

## 验证边界

配置、离线检查、SQLite backup/restore、Setup 与 Manager 测试使用临时路径、synthetic DB 和 fake keys；`python scripts/run_setup_smoke.py` 仅运行 headless 配置流程，`python scripts/run_si_manager_smoke.py` 只用 fake RuntimeController，均不调用真实 API。Native 真实进程测试仅在 Linux 使用 synthetic helper，不运行 SI Core；Windows 会跳过。Docker 静态测试不要求 daemon，不等同真实服务器运行验收；Manager fake smoke 也不是 Linux 实机验收。当前不提供自动安装/重启验收、CI/CD 或发布机制。
