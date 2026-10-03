# SI Manager v0.1 — Native Runtime Control

在项目根目录、已激活的 Python 环境安装 `pip install -e .` 后：

```bash
si
si --env-file /absolute/path/to/si.env
si setup --env-file /absolute/path/to/si.env
python -m evolving_companion.manager
```

默认进入中文 Textual TUI「SI 管理器」：启动、停止、重启、状态、日志、配置、退出。上下键/Enter 导航，`r` 刷新，Esc 回状态，`q` 仅退出管理器。worker 执行检查/控制，busy 时拒绝重复操作和退出。配置现打开临时 loopback Web Setup，显示访问 URL 和 SSH tunnel 命令；`s` 关闭 Web，`q` 等待保存完成后关闭 Web，不停止 Core。原 `si setup` 保留 SetupScreen 终端 fallback。两者复用 env 原子保存，不自动启动/重启 runtime。窄终端菜单纵向排列；内部状态值与配置字段不翻译。详见 [Deployment UX](deployment_ux_v01.md)。

## 运行与身份

RuntimeController 只有 start / stop / restart / status / logs；唯一实现为 NativeProcessController。Linux 上使用 Python 标准库，不引入第三方进程管理框架。成熟机制参考：[subprocess](https://docs.python.org/3.12/library/subprocess.html)、[flock](https://docs.python.org/3.12/library/fcntl.html)、[pidfd_open](https://docs.python.org/3.12/library/os.html#os.pidfd_open)。未复制外部代码。

Start 固定为当前 `sys.executable -m evolving_companion.server --env-file <absolute path>`，cwd 为项目根目录，stdin 为 DEVNULL，stdout/stderr 合并追加到私有日志，shell=False、start_new_session=True。不在 Manager 内实例化 Core，不改变 server 的 qq / none 分派或任何 Character/Memory/World 语义。退出 SSH/Manager 不依赖 stdin 或终端；Core 为独立进程（外部 session/cgroup 策略仍可能终止进程）。

以 resolved runtime DB 路径命名三个同目录文件，例如：

- `si_001.db.process.json`：PID、启动 ticks、Linux boot UUID、uid、cwd、argv、project/env-file/database identity；不保存密钥。
- `si_001.db.process.lock`：flock 非阻塞串行化 Start/Stop/Restart，同 DB 的多个 Manager 共用锁。
- `si_001.db.server.log`：stdout/stderr，创建/打开限制为 0600，仅普通文件，不跟随 symlink。

这些文件均被 Git ignore；要求数据库父目录已存在且操作者可写。Manager 不创建正式 DB。记录与当前 `/proc` identity 不一致或无法验证时为 Unknown，拒绝 Start/Stop/Restart；PID reuse 不当成当前实例。Verified Running 只表示进程存在，不代表 Core 初始化完成或聊天 Ready。已退出的记录为 Failed；下次 Stop 可清除，Start 可在锁内清理后启动。启动前写入 reservation；若 spawn 后身份确认/记录失败，保留 Unknown 阻止第二个 writer，不盲目杀进程或删除歧义记录。需要操作者可信核对后处理。

Stop 使用 pidfd 固定进程，再确认 birth identity，发 SIGINT 并等待最多 15 秒；不使用裸 PID kill fallback。超时保留记录并返回错误，没有默认 SIGKILL。Restart 在同一锁内 Stop → 确认退出 → Start，Stop 失败不启动新进程。最低需要可用 Linux `/proc`、flock 与 Python/kernel pidfd（Ubuntu 24.04 满足）；其他 OS 可运行 Setup/fake TUI，但不提供 Native 控制。

## 状态、环境与日志

Status 只读 seed、短 Internal UUID、记录状态和 DB；复用独立的 runtime_check/health 本地进程。strict checks 验证 keys/provider/transport，可能做临时 writable SQLite probe，正式 DB 缺失不创建它。Health 要求已存在 DB，首次启动前可显示 Error。none 合法且仍需要 API keys；qq 显示 SnowLuma / OneBot，不探测 QQ 在线或真实 API。

Manager 捕获启动时原始 OS environment；检查和 server 子进程仅继承该快照，由原入口 fresh 加载 env-file。显式 OS environment > 文件；每次刷新读取最新文件，没有全局 dotenv 叠加。Configure 更改只影响下一次检查/启动，已运行进程保持其启动配置。请先 Stop 再修改实例的 DB/env-file/project 路径；不同实例记录不被自动接管。

Logs 最多读取末尾 64 KiB / 100 行；截断首条不完整行。使用 config_env.redact 清理当前已知密钥及常见 credential 格式/控制字符，Static markup=False。不是秘密检测器：无标签历史秘密或私人对话不能保证全部被识别，日志仍须作为私人数据保护。没有 live follow 或 rotation；长期运行需操作者管理日志容量。

## 验证与限制

`python scripts/run_si_manager_smoke.py` 用临时 seed/config、fake controller/headless Textual 测试 Status → Start → Logs → Configure → Status → q。单元测试覆盖控制失败边界；Linux 专项测试仅启动 synthetic helper，Windows 跳过。当前 Windows fake/headless 验证不等于真实 Linux Manager / Docker / API / QQ 全栈验收。

Manager 只识别自己记录的进程，不能检测手动启动或 Docker 中的所有 DB writer；不要同时用多种入口运行同一数据库。Runtime record 不随私人 DB backup/restore 自动重建。独立 `install.sh` 只准备源码环境；没有 daemon、systemd/Docker backend、开机自启、崩溃 supervisor、update、SnowLuma 安装、backup/restore UI 或新的 Core 初始化流程。
