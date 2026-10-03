# A7.3 — Real Linux Deployment Validation + One-click Bootstrap

## 1. Purpose

收口 install → `si setup` → check → start → health → Manager。当前状态：**local implementation ready for Linux validation**。本轮在 Windows 实现与离线测试；没有 SSH 目标、真实 Ubuntu/systemd、重启、API 或 QQ 验收证据。

## 2. Supported Target

首要验收：Ubuntu 24.04 LTS、x86_64、systemd、初始 root SSH、2 vCPU / 2GB RAM / 约 40GB disk。沿用 Debian 12+ / Ubuntu 24.04+ 支持检查；这些额外版本也需独立实测。生产建议 2C4G 或 4C8G，API-only。

## 3. Deployment Philosophy

复用已有 A7 helpers、A7.1 Textual Manager、A7.2 Setup，不增加第二套 wizard、运维框架或 Character 功能。通用工具 Direct Reuse：uv（MIT/Apache-2.0）、发行版 systemd / apt、标准库 sqlite3 backup；自定义只含固定目录 bootstrap 与验收检查。没有复制外部源码或新增 Python 依赖。

依据 [uv 安装文档](https://docs.astral.sh/uv/getting-started/installation/)和 [0.12.22 release](https://github.com/astral-sh/uv/releases/tag/0.12.22)，直接下载官方 Linux 预编译包并核验发布 SHA256；不执行远程 shell。沿用成熟锁定环境工具，替换 bootstrap 不影响 Core/数据。相比 Docker/配置管理框架，依赖规模小、权限与退出路径可本地审阅；首次安装需信任官方 HTTPS 发布渠道，已有 uv 的来源由管理员负责。

## 4. Fresh Server Flow

在 root SSH shell（普通管理用户相应使用 sudo）执行：

```bash
apt-get update
apt-get install -y --no-install-recommends git ca-certificates
git clone https://github.com/badkon/Evolving-Companion-Agent.git
cd Evolving-Companion-Agent
# 审阅 deploy/install.sh 和所选 checkout 后：
bash deploy/install.sh
si setup
si deploy-check --offline
si health --offline
si
bash /opt/si/scripts/validate_linux.sh
```

首次获取仓库需要 git/证书；后续依赖由 installer 安装。默认部署当前 **clean、已提交 checkout 的精确 HEAD**，不默认拉 main 的另一个版本。开发产生的未提交修改必须先完成本地 review/tests/commit/release；installer 拒绝 dirty source。

## 5. One-click Installer

`sudo bash deploy/install.sh`：OS/架构/root/systemd/停止状态和 symlink 检查 → apt 最小依赖 → 创建或复用 si 用户/组与目录 → 检查/安装 uv → 部署代码 → locked API-only sync → 首次配置模板与 launcher/helpers → verify/install unit → daemon-reload → `deploy_check --offline`。关键失败按步骤停止并提示修正重跑；不启动、不 enable 服务。

可选旧用法 `sudo bash deploy/install.sh <credential-free-url> [reviewed-ref]`；支持 tag/commit。无参数还支持可信 release archive，按白名单复制 pyproject、锁、README、src、data、deploy，不复制本地 runtime/secrets。Git 来源保留 origin，目标 HEAD detached；从 release archive 安装没有 Git update，需维护者在停服/备份后部署新的受审阅 release，不能谎报 git pull 成功。

## 6. Directory Layout

```text
/opt/si/
├── app/       checkout/release + .venv + uv.lock
├── config/    si.env 与敏感配置备份
├── runtime/   si_001.db
├── logs/      仅明确需要的文件日志，服务主要用 journal
├── backups/   SQLite snapshots
├── scripts/   backup/restore/service/validation helpers
└── data/      uv 管理 Python 与缓存；非角色新设定
```

config/runtime/logs/backups 在 checkout 外，不随代码更新删除。首次没有 runtime DB 是明确的 NOT INITIALIZED 状态；检查不初始化 Character 或 DB。

## 7. Permissions

`/opt/si`、app/scripts/data：si:si / 0750；config/runtime/logs/backups：si:si / 0700；si.env：si:si / 0600。新 Git clone 后 app 归 si 以兼容既有维护流程；不是只读代码沙箱。launcher root:root / 0755；unit root:root / 0644。复用 si 前验证非 root 和 primary group si，不改已有账户身份。不使用 777，不递归重置现有 runtime/备份。

初始 root 管理 shell 可直接运行 `si`；普通 sudo 管理员使用 `sudo si` / `sudo si setup`。`si` 用户可运行配置/备份，但无自动 systemd 提权。命令在 PATH 可见不等于赋予每个主机用户读取 secrets/DB 的权限。

## 8. uv

已有 uv 复用，必须可由 si 执行；若只安装在 root 私有目录，明确失败，请安装到系统可访问位置后重跑。缺失时安装固定 uv 0.12.22 至 `/usr/local/bin/uv`，SHA256 在可审阅脚本内固定。没有 system Python pip install、curl|bash、build-essential 或 Rust 编译。

`uv sync --locked --no-dev --no-extra local-memory --python 3.12`；缓存 `/opt/si/data/uv-cache`、managed Python `/opt/si/data/python`，避免解释器依赖 root home。build/install 并发 1、download 并发 2，依据 [uv environment reference](https://docs.astral.sh/uv/reference/environment/)。uv.lock / pyproject 不一致时停止，不改锁。更新也必须使用相同目录环境变量。锁含 optional ML metadata 不代表安装 ML。

## 9. si Command

固定 `/usr/local/bin/si` wrapper 调用 `/opt/si/app/.venv/bin/si`，更新后路径稳定，无需编辑 shell PATH。无参数进 Manager，`si setup` 进既有 wizard；另提供 `si deploy-check [--offline]`、`si health [--offline]`、`si backup`、`si restore <backup-path>`，薄转发到已有 Python 模块。root 执行这四个子命令时降权为 si，验证实际读写权限并避免生成 root-owned CLI 备份；Manager/Setup 保留操作者权限，不会自动 sudo。

## 10. First-run Setup

Configure/Reconfigure → Review → Confirm Save → Deploy Check → Optional Start。Transport=None 时可以留空 API keys，保存后使用明确的离线基础检查，不能 Start；配置不完整仍不会显示 production Ready。QQ 模式仍要求现有 keys/routing 校验。未修改 Core/Character 初始化策略，秘钥仍 masked、private backup、atomic write、OS > env file。

## 11. systemd

沿用 User=si / Group=si、WorkingDirectory=/opt/si/app、EnvironmentFile=/opt/si/config/si.env、原 ExecStart、Restart=on-failure、RestartPreventExitStatus=78。installer 不启动/enable；None 正式服务启动仍失败，不能作为无网络运行模式。部署完整且明确选择真实运行后，由 Setup 可选 Start 或管理员 systemctl start；需要开机运行再显式 enable。

## 12. Offline Validation

Level 1：`sudo bash /opt/si/scripts/validate_linux.sh`。检查 OS/arch/systemd、uv、unit syntax 与 service state；以 si 执行 Python 检查 owner/mode、installed unit 一致性、venv/import、稳定 launcher、无 ML 包、seed UUID、配置/目录与 SQLite。复用 `check_deployment(..., offline=True)`，没有 API/transport 请求。

缺 keys 显示 DEFERRED；None 合法，未知 transport/错误 provider/坏 DB 等仍失败。首次 health --offline 明确 NOT INITIALIZED，不创建 DB、不证明 production health。默认严格 deploy-check/health 和 server 检查保持原要求。

还在 backups 下创建/删除私有临时 synthetic SQLite，使用现有 backup/restore 检查 UUID 与 pre_restore 内容；不操作真实 runtime DB、Memory 或 Character。Shell/Python PASS 只证明自动检查通过，不能替代实际交互与 reboot 验收。

## 13. API Validation

Level 2 独立、用户显式填入 DeepSeek/SiliconFlow keys 后按既有真实 smoke 验证。安装、pytest、deploy_check、health 不发真实 API 请求。未填 API key 不表示 Linux foundation 失败；离线通过也不表示 API 正常。

## 14. Transport Validation

Level 3 独立验证 QQ / OneBot / SnowLuma，由用户配置外部 transport/login。None 不依赖 QQ，正式长期 service 仍要求 QQ。installer 不登录 QQ、启动 SnowLuma 或增加新 transport。

## 15. Backup

已有有效 runtime DB 后：`si backup` 或 Manager Create Backup。使用 Connection.backup()，不 cp 正在写入 DB。首次尚无 DB，真实 backup 明确失败；离线验收使用第 12 节临时 probe，不硬造正式 Character 数据。

## 16. Restore

停止 service 和其他 writer，再 `si restore /opt/si/backups/<explicit>.db`，或 Manager 确认。保留 stopped/UUID/integrity/pre_restore 保护，不自动启动。随后 deploy-check/health；没有 runtime DB 时不允许无保护 restore。新机迁移按 A7 从已验证 snapshot 安全传输并设私有权限。管理员必须防止同时启动/写入，既有 TOCTOU 限制保留。

## 17. Update

沿用手动、前台、失败即停顺序：**stop → backup → update code → locked sync → deploy_check → start → health/status**。没有新增自动 updater。

先确认 app 是 Git working tree、`git status --porcelain` 为空，拒绝 dirty/local changes；不 reset/clean。取受审阅 ref（tag/commit，不锁 main），网络 fetch/pull 失败立即停止，保持停服。默认 detached HEAD 应 fetch 后 checkout 指定 ref，不能无条件 git pull。

sync 由 si 执行，设置 `HOME=/opt/si UV_CACHE_DIR=/opt/si/data/uv-cache UV_PYTHON_INSTALL_DIR=/opt/si/data/python UV_CONCURRENT_BUILDS=1 UV_CONCURRENT_INSTALLS=1 UV_CONCURRENT_DOWNLOADS=2` 并保持既有 --locked API-only flags。unit/helpers 变化时停服重跑 installer，它保留 checkout/config/DB/backups并安装当前资产；之后必须严格 `si deploy-check`，通过才显式 start。None 离线环境不执行 start；`--offline` 成功不得用于授权生产 restart。update/fetch/check 失败保留备份与停止状态，人工恢复可信代码环境，再重新检查，绝不失败后继续 start。

## 18. Reboot / Persistence

真实验收记录 ref、seed UUID、env 权限、备份列表，以及已有 DB 的只读 integrity/UUID；reboot 后重复 validation/Manager。未明确 enable 的服务重启后停止是预期。只有已有正式 DB 时才验证其内容保留；首次无 DB 不能声称 runtime continuity 已实测。

无密钥新机可由管理员在 `/opt/si/data/reboot-probe.db` 用 sqlite3 创建独立 synthetic marker、设 si 私有权限，reboot 后检查相同 marker 并移除该测试文件。这只验证磁盘文件持久化，不生成 Character，也不替代正式 DB 迁移验收。

## 19. Security Notes

不改 SSH/sshd_config、root login、密码认证、防火墙、端口、swap/fstab。验证完可由管理员独立采用 SSH key、sudo 管理账户和关闭 root 密码远程登录。现有 Core 只做 outbound API/forward WS，通常无需新增 inbound 端口；独立 OneBot server 的配置见其文档。journal 是主要服务日志，Manager 沿用近期 journal + secret redaction。不打印 env/keys；不向 Git 放私有数据。

开发工作流：Local PC Codex/VSCode → tests → review/commit → push/release → server update → health。服务器只负责运行与运维，不需要 Codex/IDE，不建议 SSH 修改 production code。

## 20. 2GB Test Host Notes

API-only，不安装 local-memory、torch、本地模型或 Godot。不并行 build/install，不在新服务器跑 pytest -n auto。仅 apt ca-certificates/curl/git/sqlite3；其常规系统依赖由 apt 处理。不引入 Docker/Node/Nginx/Redis/PostgreSQL。可选 1GB swap 属主机策略，installer 不创建。安装仍需网络、磁盘空间；真实峰值 RAM 尚未测量。

## 21. Failure Recovery

按失败 STEP 修复网络、发行版、账户、权限或锁后重跑；已有 uv/env/DB/backups 不重置，已有 checkout 不 pull/reset。首次 clone/copy 部分失败留下非空 incomplete app 时拒绝继续，维护者检查其内容后再处理；不自动清空目录。sync 失败不安装后续 unit/launcher，不启动服务；unit/check 失败可能保留已安装资产，重跑修正，不能声称主机修改全量 rollback。下载临时包只清理本次 mktemp 的固定文件。

如果源 checkout 已更新但目标 app 已存在，installer 明确保留目标；必须先按更新流程切换目标 ref。重装不会因为传入不同 URL/ref就替换旧实例。

## 22. Acceptance Checklist

- [ ] 干净 Ubuntu 24.04 / 2C2G 安装成功，记录 ref/uv/Python/version/resource evidence。
- [ ] 固定目录、si 账户、owner/mode、launcher/unit 正确，重复安装保留 env/DB/backups。
- [ ] si setup：无 keys、None、Review/Save/Check/Skip Start 成功；root SSH TUI 可退出。
- [ ] offline check/health/validation 成功，未发 API/QQ 请求，无本地 ML 包。
- [ ] 临时 SQLite backup/restore/pre_restore/UUID 验证通过；已有正式 DB 时另验证真实 backup/restore。
- [ ] reboot 后代码/config/测试 DB 或已有 runtime DB 保留，Manager 可重新进入。
- [ ] 验证 dirty update/网络失败停止且不 restart；记录人工恢复流程。
- [ ] Level 2 API、Level 3 transport 分开填写验证证据。

当前以上 **real Linux checklist 尚未执行**。Windows 自动测试与资产审查不能替代 systemd sandbox、主机权限、SSH 交互、reboot 与真实依赖下载验收。
