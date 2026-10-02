# A7 — Deployment Foundation v0.1

## 1. Goals

提供 Linux Server 的 install → config → check → start 基础，以及离线 health、SQLite-safe backup/restore。不是 TUI、Wizard、WebUI 或自动运维产品。当前代码已落盘并在 Windows 离线测试；没有声称真实 Linux/systemd 或真实 QQ 部署已验证。

## 2. Architecture Boundary

Deployment Layer ≠ Character Core ≠ Character ≠ Device ≠ Transport ≠ World ≠ Memory。Device Migration ≠ Character Reset。配置/检查/备份操作不能生成身份或重建角色心理状态。
通用能力 Direct Reuse：[uv 项目锁定与环境管理](https://docs.astral.sh/uv/guides/projects/)（MIT/Apache-2.0）、[systemd service](https://manpages.debian.org/bookworm/systemd/systemd.service.5.en.html)（发行包 GPL/LGPL 许可）、[Python sqlite3 backup](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup)（标准库 PSF/SQLite public domain）。它们成熟、边界明确、无需在 Core 新增运行依赖；没有复制外部源码或加入框架。相比 Docker/完整运维框架，此阶段直接使用目标系统进程管理、已有 dotenv 和标准库；后续替换部署工具不改变 Character 数据。

## 3. Supported Platform

目标为 Debian 12+ / Ubuntu Server 24.04 LTS+、Linux x86_64、systemd。Python 3.12 由 uv 选择/必要时下载，不依赖 Debian 12 的系统 Python 版本。暂不专门支持 ARM、GPU、Windows Server、macOS、容器/Kubernetes。

## 4. Directory Layout

```text
/opt/si/
├── app/          Git checkout，项目 .venv 与 uv.lock
├── config/       si.env；独立于 checkout
├── runtime/      si_001.db；独立于 checkout
├── logs/         保留给可选文件日志，当前用 journal
├── backups/      SQLite snapshots；独立于 checkout
├── scripts/      薄 shell helpers
└── data/         预留给确有需要的部署数据副本，当前不复制 seed
```

uv 自己管理用户缓存/Python 安装目录；不硬编码 /opt/si/venv。默认 app/.venv。SI_DEPLOY_ROOT 用于 Python helper 的显式路径配置，默认 /opt/si；本版 installer/unit/helpers 固定 /opt/si，不是任意前缀部署产品。

## 5. Dedicated User

installer 以 root/sudo 操作系统资源，创建无登录 shell 的系统用户/组 si。Core 始终 User=si、Group=si；server entry 拒绝 Linux root，非 Linux 也拒绝。目录 app/scripts/data 为 0750，config/runtime/logs/backups 为 0700，均具备 si 必要权限；不使用 777。

## 6. uv Environment

要求先安装可由 root 和 si 使用的 uv。installer 检测缺失后清晰退出，不自动下载执行远程脚本；来源与受控安装方法见 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)。可下载安装器、检查来源/内容后由维护者执行，不使用不透明 curl|sh。

```bash
uv sync --project /opt/si/app --locked --no-dev --no-extra local-memory --python 3.12
```

uv.lock 由 uv 生成并提交，包含可选依赖的解析 metadata；不代表 optional extra 被安装。服务器只 sync core，无 local-memory。锁文件与 pyproject 不一致则拒绝 sync，不在服务器静默升级依赖。锁文件本轮以 uv 0.12.22 生成；维护者应使用兼容该 lock revision 的 uv。代码 ref 与锁文件一起固定，才是可重复环境；本轮 API-only dry-run 已验证，未实测 Linux 安装。

## 7. API-only Server Profile

LLM 为 DeepSeek API；Memory 为 SiliconFlow Qwen/Qwen3-Embedding-8B（4096）及 Qwen/Qwen3-Reranker-8B。推荐值在 deploy/si.env.example，未写死进 Character Core。生产 code default 仍 local/local，Server 检查要求 api/api。
不安装 torch、sentence-transformers、transformers 或 HF 模型缓存；local-memory 继续用于本地开发/benchmark/未来独立 fallback 实验。无自动 local fallback。

## 8. Secrets

服务器配置 /opt/si/config/si.env，owner si、0600，Git 忽略 si.env；模板仅含空密钥。用编辑器填写 DEEPSEEK_API_KEY、SILICONFLOW_API_KEY 和 QQ routing；无终端交互式密钥输入。
入口使用已有 python-dotenv，无 shell source；OS/process 同名变量优先，禁用变量插值。systemd EnvironmentFile 不执行 shell expansion，因此模板不使用 ${SILICONFLOW_API_KEY}；deployment loader 将该 key 映射到未显式设置的两个 generic SI_MEMORY_*_API_KEY。显式 generic key 不覆盖。
server 不读取 app/.env.local；开发 CLI/QQ 默认路径保留原行为。check/health 只输出 CONFIGURED/MISSING 或安全异常类型，不输出文件内容、keys、headers、完整 validation errors。systemd env 文件规则参考 [systemd.exec](https://manpages.debian.org/bookworm/systemd/systemd.exec.5.en.html)。

## 9. Runtime DB

SI_RUNTIME_DB=/opt/si/runtime/si_001.db。SQLiteStore 无显式 path 时读取该变量；显式 path 优先，未配置时开发默认仍 runtime/si_001.db。QQ entry 同样读取外置路径。
Server check 要求显式绝对 DB 路径且在 app checkout 外；不将 DB 复制进 app、不由 git pull/reinstall 覆盖。check 只用一次性 probe DB 验证可写性，真实 DB 首次由正常服务启动创建。Seed 与 Runtime State 分离。

## 10. systemd

deploy/systemd/si.service 的真实 ExecStart 为 /opt/si/app/.venv/bin/python -m evolving_companion.server。入口按 SI_CHAT_TRANSPORT=qq 选择已有 QQ transport；其他值明确拒绝，不设计 registry。
Restart=on-failure，RestartSec=5，配置错误退出 78 且 RestartPreventExitStatus=78；60 秒最多 3 次启动。QQ entry 现在向 server 返回启动/运行失败 exit status；耗尽既有 reconnect 后正常返回也作为长期服务结束错误返回 1，KeyboardInterrupt 返回 0。原对话/网络重连算法不改。unit 使用 UMask=0077、NoNewPrivileges、PrivateTmp、ProtectSystem/ProtectHome，只允许 runtime/logs/backups 服务写入。不暴露 Core HTTP。

## 11. Service Lifecycle

维护者先 review 可信仓库代码，然后运行：

```bash
sudo bash deploy/install.sh <credential-free-repository-url> <reviewed-ref>
# 首次 check 因空 keys/routing 失败是预期结果；基础安装已完成，不自动启动。
sudo -u si editor /opt/si/config/si.env
sudo -u si /opt/si/app/.venv/bin/python -m evolving_companion.deploy_check
sudo systemctl enable si.service
sudo systemctl start si.service
sudo bash /opt/si/scripts/si-service.sh status
journalctl -u si
journalctl -u si -f
```

替换 editor 为实际编辑器。installer 仅首次 clone，已有 checkout 不 pull/reset；已有 env 不覆盖，DB/backups 不删除。它安装 unit/helpers 并 daemon-reload，但不自动 enable/start；运行中的服务需先停。si-service.sh 只是 systemctl/journalctl wrapper，不是 process manager。

## 12. Deploy Check

python -m evolving_companion.deploy_check（可显式 --env-file）。检查文件/锁、0600+si env、Character/World seed、DB 路径分离和目录写权限、SQLite probe/open/integrity、api/api 配置、required keys、transport URL与routing、app version metadata。
所有检查完全离线，不实例化 LLM，不调用任何第三方或 QQ。已有 DB 仅 mode=ro 读取，不做 schema migration/seed 初始化；如 State/Time/Life 的持久化 UUID 与 seed 不同则拒绝，防止更新后静默创建另一个角色。不存在 DB 时只允许正常启动后初始化，不伪造健康状态。

## 13. Health

python -m evolving_companion.health（可 --env-file）复用检查函数，但要求真实 runtime DB 已存在并只读完整性检查。目录探测仍会创建/删除临时 probe，不改真实 DB。失败返回非零；结果不是“第三方服务在线”“QQ已登录”或 systemd 服务状态的证明。没有 HTTP health server。

## 14. Backup

```bash
sudo -u si bash /opt/si/scripts/backup.sh
```

backup_database() 通过 sqlite3.Connection.backup 从只读源生成 /opt/si/backups/si_001_YYYYMMDD_HHMMSS.db（UTC），能安全包含已提交 WAL 数据，不 cp live.db。源存在且有有效 SQLite header/quick_check 才备份；新文件独占创建、0600，同秒冲突拒绝覆盖。失败只清理本次未完成输出，不删除历史备份。API 可供未来 Manager 调用。

## 15. Restore

```bash
sudo systemctl stop si.service
sudo -u si bash /opt/si/scripts/restore.sh /opt/si/backups/<explicit-backup>.db
sudo -u si /opt/si/app/.venv/bin/python -m evolving_companion.deploy_check
sudo systemctl start si.service
```

CLI 必须显式 backup path；查询 systemd 确认 inactive/failed（命令不可用或未知状态时拒绝），验证源/目标 SQLite 及已有 State/Time/Life Character UUID 集合一致，然后先生成 pre_restore_YYYYMMDD_HHMMSS.db，再通过 SQLite backup API 写入目标。目标现有 DB 必须有效，拒绝隐式创建/无保护覆盖；不自动启动服务。缺少 Character-owned UUID 的早期 DB 无法靠此检查识别归属；必须由维护者确认来源。可复用 Python API 要求调用方确认 service_stopped=True。维护者也须关闭其他 CLI/DB writer，systemd 状态不是跨进程锁。
本版新服务器迁移：服务保持停止，把已完成且验证过的 snapshot 安全传输到 runtime/si_001.db，调整 si owner/0600，再 check；不要复制运行中 DB。已有 DB 的恢复必须走带 pre-restore 保护的 restore。无自动 rollback/retention。

## 16. Update

停止服务 → backup → 明确更新 app 代码 ref → uv sync --locked --no-dev --no-extra local-memory --python 3.12 → deploy_check → start → health/status。git 操作仅限 app，不碰 config/runtime/backups。先处理 dirty checkout，不强制 reset/clean。
需要 unit/helper 变更时由维护者重新安装对应 deploy 资产、daemon-reload（或服务停止后重跑 installer，它保留已有 checkout/env）。不实现自动 updater、self-update 或后台 rollback。

## 17. Persistent Data Contract

跨更新、重启、reinstall、迁移必须保留：seed 的固定 identity.internal_id、整个 runtime SQLite（Memory/Evidence/Supersession/Archive、State、Time anchors、Life、World references）、config/secrets、backups。Seed 当前来自 app repository，代码更新必须保留 UUID；不在部署流程生成或 hash UUID。
代码、Python env、派生 embedding index ≠ 角色身份。World/State/Life 正常启动沿既有只初始化缺失记录的逻辑，不覆盖已持久化内容。部署检查不执行这些初始化。迁移也须安全转移配置与可信 seed/code ref，不只复制 DB。

## 18. Failure Semantics

A6.3 保持：Need=true recall infrastructure failure → memory_recall_failed → 本轮无 Memory 区块 → 主 Character 回复继续。成功回复后的 formation/consolidation 仍 best-effort。main LLM、user Archive、assistant Archive 失败仍是真正本轮失败，不加入多 LLM/provider fallback。降级不会缩短 API timeout 等待。

## 19. Security

非 root Core，env 0600，DB/backups 私有，不修改 SSH/firewall、不开放任何 Core 公网端口。网络仅现有 outbound API/transport；OneBot 服务独立部署，不由 installer 登录/启动 QQ。使用无 credential 的 repository URL，拒绝 symlink 部署目标；不要把 secrets 放 URL、命令行或 app repository。
systemd 是唯一进程管理器；没有 arbitrary shell API、公开 admin、reverse proxy 或陌生脚本自动执行。installer 执行可信 app 的依赖构建，因此必须先 review 来源/ref；不是对恶意仓库的沙箱。

## 20. Known Limitations

Windows tests 只覆盖 Python/helpers、assets 静态契约和 mocks，不代表 systemd sandbox、权限、真实发行版安装或网络可用性已实测。当前机器没有 bash，bash -n 未执行；上线前在目标 Linux 上执行四个 deploy/*.sh 的 bash -n 和 systemd-analyze verify deploy/systemd/si.service。
依赖锁记录 ML extra 的可选 metadata，但 server 不安装；没有 backup rotation/cron、自动更新、自动恢复、监控或 HA。恢复状态检查存在 TOCTOU：管理员必须保持服务停止并阻止并发 writer/start。首次缺 config 时 check 会失败，填配置后重跑。不能将健康输出当成真实对话能力证明。

## 21. SI Manager / Next Steps

A7.1 已增加 `si` Textual TUI，复用 check_deployment、backup_database、restore_database 和 systemd，管理已部署实例的状态/启停/日志/备份/恢复；配置只读，无自动更新或安装器替代。把 `/opt/si/app/.venv/bin` 加入 SSH 用户 PATH 后运行 `si`。详细权限、离线语义及限制见 [SI Manager TUI](a7_1_si_manager_tui_v01.md)。下一步 A7.2 First-run Setup / A7.3 Developer Console 尚未实现。
