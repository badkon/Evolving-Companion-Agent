# A7.1 — SI Manager TUI v0.1

## 1. Purpose

Manager 面向开发者/运维者，管理已经完成 A7 部署的实例。不是 Character 的界面、Chat Client、Memory UI 或 Developer Console，不改变 Character Core。

## 2. Scope

提供 Overview、Service、Deployment Check、Health、只读 Configuration、Backup / Restore、Recent Logs、Exit。没有安装向导、secret editor、自动更新、WebUI、Memory/State/World 编辑。

## 3. Architecture

`si → SIManagerApp → DeploymentFacade / SystemdServiceManager → A7 Python helpers / systemd`。Widget 不直接 subprocess；复用 `check_deployment(..., health=...)`、`backup_database()`、`restore_database()`，无新 config parser、process manager 或 plugin framework。

通用 TUI 为 Direct Reuse：[Textual](https://github.com/Textualize/textual)（[MIT License](https://github.com/Textualize/textual/blob/main/LICENSE)），持续维护，支持终端键盘、Unicode、headless [Pilot tests](https://textual.textualize.io/guide/testing/) 与异步 [workers](https://textual.textualize.io/guide/workers/)。Rich 单独更适合输出；curses 的跨平台测试/中文布局需更多自定义工作。本版只直接新增 Textual，无 Web/ML stack；Rich 等由它正常传递引入。UI 依赖不进入 Core，替换 UI 不改变数据和 A7 API；未复制外部源码。

## 4. Entry Point

console script：`si = evolving_companion.manager:main`。A7 API-only `uv sync --locked --no-dev --no-extra local-memory --python 3.12` 安装它。

Linux SSH 会话：

```bash
# A7.3 安装 /usr/local/bin/si；root 管理 shell 或 sudo si
si --help
si
# 或直接：
/opt/si/app/.venv/bin/si
```

A7.3 无需维护者编辑 PATH；普通 sudo 管理员可用 sudo si。不要把 Core 改为 root；Manager 操作者权限与 Core 的 si 用户分离。默认 `/opt/si/config/si.env`，可 `--env-file`；入口复用 A7 loader，不读取开发 `.env.local`。Windows 提示 Linux deployment 并退出，不访问 /opt/si。

## 5. Overview

A7.2 后增加 Setup Required / Ready 与 Run Setup 入口；Configuration 的 Reconfigure 共用同一 SetupScreen，不复制 UI。Setup 保存后更新 file-derived 环境，显式 OS overrides 保持；不会自动重启 Core。

显示工作名/开发代号、短 UUID、Running/Stopped/Failed/Unknown、DB Available/Missing、check/health OK/Error、Memory Profile、transport、已有 app version。全部本地，不读取 Memory 内容或初始化 DB。
方向键/Enter 选择菜单，Tab/Shift+Tab 移动按钮/备份列表；`r` 刷新、`Esc` 返回 Overview、`q`/Exit 退出。执行中禁止重复操作及退出，避免中断 SQLite backup/restore。

## 6. Service Management

固定参数 `systemctl show si.service --property=ActiveState --value`；动作使用 `systemctl --no-ask-password <fixed-action> si.service`。不接受任意服务名、命令或 shell，捕获 exit code/15 秒 timeout，显示 permission denied / sudo required 或 unavailable，不自动提权。进入页面/刷新才查询，不逐帧轮询。未知/过渡状态为 Unknown。

## 7. Deployment Check

复用 A7 离线逐项 OK/ERROR，包括 seed、目录、SQLite、keys、Memory provider、transport；会创建/删除临时 writable probe，但不创建/修改真实 runtime DB。

## 8. Health

复用 A7 health 参数并要求 DB 存在。Health OK 不表示 DeepSeek、SiliconFlow、QQ 或其他 transport 在线，无 connectivity test。

## 9. Config

A7.2 通过独立 [First-run Setup](a7_2_first_run_setup_v01.md) 安全编辑白名单字段/凭据；本页仍只读，不提供任意 env editor。

只读白名单：provider/model、runtime DB、transport、secret 文件位置。Key 仅 Configured/Missing，不把整个 environment/env 文件放进 UI model；展示文本过滤已知 secret 和控制字符。编辑延后，不写坏未知字段/secret。外部配置修改后需退出重进；刷新不重新加载文件，仍保留 OS > env-file 优先级。

## 10. Backup / Restore

仅列 A7 命名 `.db`，显示文件名、UTC 文件时间/大小，忽略 symlink/无关文件。Create Backup 复用 SQLite online backup，成功刷新；不复制 live DB、不删除旧备份。
Restore 选现有备份后弹出确认，默认焦点 Cancel，Esc/q 取消。确认后重新查询服务，仅 inactive/failed 可进入原 restore；原 SQLite/Character UUID/pre_restore 保护不变，失败显示错误而不 crash。无自动 stop/start。
管理员须停止其他 writer 并阻止并发 start；systemd 状态检查不是锁，保留 A7 TOCTOU 限制。

## 11. Logs

固定 `journalctl -u si.service --no-pager --lines=100 --output=short`，Recent + Refresh，不 live follow。所有阻塞操作经 worker thread，不阻塞 UI。展示前屏蔽已配置凭据值、常见 Authorization/key/token/password 格式及控制字符，禁用输出 markup。

## 12. Security

无 key 编辑/显示、完整 UUID 主展示、shell 输入或自动 sudo；维护者自行配置受控 systemd/journal 和私有文件访问权限。异常只记录 operation + exception type，不打印含私人输入的 traceback、validation error 或原始 stderr。
日志可能含私人运行信息；屏蔽并非任意未知 secret 的完备检测。不得把 secret 写进源日志或随意共享 journal 截图。Manager 需要 config/runtime/backups 的必要权限，不负责安装/修复未部署实例。

## 13. Offline Semantics

只访问本地文件、SQLite、systemd/journal；不实例化 LLM/Chat Transport/本地模型，不调用 Internet/API。测试拦截 network/subprocess，仅使用 tmp_path 合成 DB。

## 14. Limitations

配置只读、日志不 follow；无 Update Now、Git pull、rollback、installer replacement，仅显示 app version，不自动读 Git revision。Windows headless 测试不证明真实 Linux systemd/polkit/SSH 实测。
离线 smoke：`python scripts/run_si_manager_smoke.py`，fake Running → Service → Health → Overview → q，不访问真实 DB/secrets/systemd/network。

## 15. First-run Setup

A7.1 为 ongoing operations。A7.2 已实现 `si setup` 的 masked secrets、API-only profile、QQ/None、Review、atomic save、离线 validation 和可选 first start；见 [A7.2 文档](a7_2_first_run_setup_v01.md)。Core、World、Memory 不由 Setup 初始化/重置。

## 16. Future Developer Console

A7.3 独立研究 Memory/State/World inspection、debugging、runtime observability。本阶段未实现 WebUI/HTTP 或对应空模块。
