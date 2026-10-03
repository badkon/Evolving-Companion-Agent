# Docker Server Deployment v1

当前仅支持 Linux Server 的 SI Core Compose 运行基础。Docker 是 Runtime Host，不是 Character；没有新业务 runtime。普通 pytest 不要求 Docker，真实容器验收需在有 Docker 的 Linux 环境另行执行。

## 方案与复用

参考 [MaiBot 官方部署文档](https://docs.mai-mai.org/en/manual/deployment/docker) 的 Compose + 宿主机持久化思路，但不复制其代码、目录、数据库或附带服务；这是 Design Reference，不引入外部代码或许可证负担。直接复用成熟的 Docker/Compose 标准能力，维护成本集中于一个 Dockerfile 和一个单服务 Compose；项目依赖仍唯一来自 pyproject.toml，没有新增框架或 Python 依赖。原生配置/SQLite 可脱离容器继续使用，迁移不依赖容器层。

## 前置与配置

宿主机已安装 Docker Engine 和 Docker Compose v2，并取得可信项目 checkout；不提供 Docker 自动安装或一键 installer。检查：

```bash
docker --version
docker compose version
```

在项目根目录，仅首次配置时复制模板（已有文件不要覆盖）：

```bash
mkdir -p runtime backups
cp -n config/si.env.example config/si.env
chmod 600 config/si.env
```

编辑 `config/si.env`，填入 DEEPSEEK_API_KEY、SILICONFLOW_API_KEY，设置 `SI_CHAT_TRANSPORT=qq`、bot user ID、明确的 allowed user IDs 和外部 OneBot WS 地址/可选 token。None 仅可用于离线检查，不是 server 的聊天模式。不要打印配置、把 secrets 写进 Dockerfile 或提交真实配置；容器不加载开发 `.env.local`。

现有 runtime_config/config_env 继续生效：容器显式环境变量 > 挂载配置文件。Compose 固定 api/api、数据库 `runtime/si_001.db` 与 `backups` 路径，避免配置指向容器非持久化位置。其他 provider/model 参数沿用模板和既有 API adapter；Docker 不新增 provider。宿主机环境不会自动传入容器；需要显式覆盖时使用 Compose override 的 environment 或一次性 `docker compose run -e NAME=...`，不要提交真实秘密。

## 构建与运行

```bash
docker compose build
docker compose run --rm --no-deps si-core python -m evolving_companion.runtime_check --offline
docker compose up -d
docker compose ps
docker compose logs --tail=100 -f si-core
docker compose stop si-core
docker compose start si-core
docker compose restart si-core
```

依赖只在 image build 时安装，没有 startup pip install、Git checkout、venv/uv 或服务管理器。基础镜像 Python 3.12 slim，安装项目不带 dev/local-memory extras，因此不安装本地模型依赖或下载模型权重。editable 安装是为了保持当前 `src` layout 的 `/app` seed 资源定位；生产源码由 image 固定提供，不挂载宿主机源码。

默认 command 是 `python -m evolving_companion.server --env-file config/si.env`，校验配置后调用已有 QQ runtime。PID 使用 Compose init 管理；无交互输入，退出后按 unless-stopped 策略重启。错误配置可能重复退出，应查看日志并修正配置。配置文件由应用启动时加载，修改后重启。

### 外部 Transport

Compose 只包含 si-core，不负责 OneBot 安装、登录或启动，也不声称完成真实 QQ 验收。当前采用 Linux host networking，因此模板的 `ws://127.0.0.1:3001/` 指向宿主机服务；远程 OneBot 使用实际可达地址。无需暴露 SI 监听端口，服务没有 WebUI。

host networking 牺牲网络命名空间隔离，不能当作完整网络沙箱。OneBot 应仅对可信网络开放并按需要配置 token。容器默认使用镜像的 root 用户，不创建宿主机账户、不做 chown 协调；文件写入仍受挂载和权限限制。Compose 使用只读根文件系统、只读 config/data、drop all capabilities、no-new-privileges，未使用 privileged 或挂载 Docker socket。宿主机 Docker 权限与私人目录保护仍由操作者负责，不能将此基础视为全面安全审计。

## 持久化合同

| 宿主机（Compose 文件所在目录） | 容器 | 内容 |
| --- | --- | --- |
| `config/` | `/app/config`，只读 | si.env、模板及已有私有配置备份 |
| `data/` | `/app/data`，只读 | 固定 Internal UUID 的 Character seed、World seed |
| `runtime/` | `/app/runtime`，可写 | si_001.db、SQLite WAL/SHM、既有 Character/Memory/World runtime |
| `backups/` | `/app/backups`，可写 | 手工 SQLite backup 与 pre_restore 文件 |

所有目录必须存在；create_host_path=false 防止拼错路径时静默创建空目录。宿主机 data 必须包含原 Character/World seed，重建前不要用不同 UUID 的 seed 替换；已有 DB 与 seed 不一致会被检查拒绝。默认 image 内也包含版本化 seed，但正式 Compose 用宿主机只读 data 覆盖它，容器更换不会替换该 identity。

保留这些宿主机目录、相同 UUID seed 且使用相同映射，是重建保持连续性的前提。`docker compose down` / 重建不删除 bind-mounted 文件；删除目录、换 checkout 路径或手动清空 DB 仍可能造成数据丢失。不要同时启动多个 SI 实例写同一 DB，也不要将旧私有目录上传到 Git；Git 已忽略 DB 和 si.env，`.dockerignore` 的 allowlist 不发送这些内容到构建上下文。

## Health 与手工备份

Healthcheck 复用 `python -m evolving_companion.health --offline`：只验证本地配置、seed、目录/SQLite 与已有 DB 完整性/identity，不调用外部 API、不建立 WS、不初始化正式数据库。首次 DB 尚不存在时报告 NOT INITIALIZED，不强制创建。它是应用基础检查，不证明事件循环无阻塞、远端 provider 在线或 QQ 消息可送达。容器进程退出由 restart 策略处理；仅 unhealthy 不触发 Compose 自动修复。[Docker Compose 语义](https://docs.docker.com/reference/compose-file/services/)

```bash
docker compose exec si-core python -m evolving_companion.backup
# 恢复前必须停止全部写入者（包括宿主机或其他容器实例）
docker compose stop si-core
docker compose run --rm --no-deps si-core python -m evolving_companion.restore backups/CHOSEN_BACKUP.db --writers-stopped
docker compose up -d
```

backup 使用既有 SQLite backup API，不复制活跃 DB 文件。恢复继承已有完整性/UUID 检查和 pre_restore 保护；确认标志不是自动锁定或进程检测。具体边界见 [Runtime Operations](runtime_operations.md)。没有自动 backup 编排或自动 rollback。

## 当前限制

本阶段没有 Manager、si start/stop/update、发布镜像/GHCR、CI/CD、一键安装器、更新 rollback、服务器迁移、OneBot 自动安装或真实 QQ 验证；没有 Phase C 或 Character/Memory/World 新功能。image 当前由操作者在本地构建，不是已发布的预构建镜像；依赖使用现有版本范围，没有宣称可复现的锁定构建。静态测试通过不等于真实 Linux Docker 验收通过。
