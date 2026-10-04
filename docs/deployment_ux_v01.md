# SI Deployment UX v0.1

本文保留 Native 源码部署闭环；Web Setup 已重组为 [SI Console v0.1](si_console_v01.md)。用户报告上一版 Linux 安装、Manager、Web Setup、API 测试及 Core 控制已通过，SnowLuma Docker 已部署；真实 QQ 私聊尚未最终验收。本轮 Console 不据此宣称已完成实机验收。
Core、Conversation、Memory、World、SQLite schema、QQ/OneBot 处理语义不变。

## 首次运行

在可信 checkout 根目录，使用普通操作者账户（Python 3.12+，Ubuntu 需 `python3.12-venv`）：

```bash
bash install.sh
export PATH="$HOME/.local/bin:$PATH"
si
```

安装器创建/复用 `.venv`，执行 `pip install -e .`（无 local-memory extra），创建 config/runtime/backups，
仅在不存在时复制 env 模板。生成当前用户的 `~/.local/bin/si` launcher，固定 cwd 到当前 checkout。
`SI_INSTALL_PYTHON` 可选择 Python，`SI_BIN_DIR` 可选择 launcher 目录；不同 checkout 的 launcher 不静默覆盖。
重复运行保留现有 config、Character seed、runtime、backups；不 clone/update、不改账户/ownership、不创建 systemd。
网络或 Python 环境失败修正后可重跑；脚本不会回滚/删除用户数据。已有损坏 venv 需操作者处理，不自动清空目录。

1. Manager → 配置，等候显示本机 URL 和 SSH tunnel 命令。
2. 在自己的电脑执行 `ssh -N -L PORT:127.0.0.1:PORT user@server`（用管理器显示的端口，替换真实 SSH 目标）；保持窗口开启。
3. 浏览器打开管理器显示的完整 URL（包括 `#` 后临时访问凭证），进入 SI Console，在模型与服务 / 聊天管理填写配置。
4. 分别测试语言模型、Embedding、Reranker、OneBot；测试会产生极小 API 调用费用。
5. 保存应用配置 / 角色配置，再执行“检查配置”。保存不自动启动，也不声称已通过检查；可保存缺少密钥的草稿，Start 仍严格拒绝不完整配置。
6. Console → 运行与日志 → 启动，或返回 Manager（Esc）→ 启动 → 状态 / 日志。运行中进程保留旧配置，修改后需显式重启。
7. `s` 关闭临时网页服务，`q` 退出 Manager：等待进行中的保存完成，只关闭 Web，不停止独立 Core。

不需要开放公网 HTTP 端口。`si setup` 仍是原有终端配置 fallback，不包含新增 Character/连接测试界面。
Console 用 fragment token 换取临时 HttpOnly / SameSite=Strict 会话 cookie，刷新和多页导航无需重复贴 token。关闭再开启服务会换 token/端口/会话；cookie 失效后请重新打开管理器的完整链接。

## 数据与架构

`Manager Configure → WebSetupServer(loopback/thread/Uvicorn) → WebSetupService → ApplicationEnvService`

配置仍只有 `config/si.env`。Manager 原始 OS environment > 当前文件，每次检查/启动重新读取。
Web Validate 使用原 `runtime_check` 隔离进程，传入当前候选配置；不会修改 Manager 的全局环境。
Character 仍只有现有 YAML → Pydantic → Projection；不新增 Character schema、秘密数据库或 runtime 初始化链。
Console 提供 Core start/stop/restart API，委托同一个 ManagerService / NativeProcessController，不复制进程管理逻辑；记忆浏览通过只读 SQLite 连接，不初始化 Store。

LLM 增加可选 `SI_LLM_API_URL` / `SI_LLM_MODEL`，缺省保持原 DeepSeek URL/model。
目前是同一个 OpenAI-compatible adapter，不是多 provider router。密钥继续是 `DEEPSEEK_API_KEY`。
Memory 只配置现有 API embedding/reranker；默认 SiliconFlow Qwen3，能改 endpoint/model/dimension，
不下载 BGE，不导入 sentence-transformers/torch。换 embedding model/dimension 的既有缓存行为不变。

## Character 映射与边界

- Name → identity.working_name。正式姓名、UUID、开发代号、阶段、continuity 全部只读，Raw YAML 也不能改。
- Personality → baseline_traits。
- Speaking style / relationship baseline → 现有 social_tendencies（三种社交情境），不另造字段或承诺自动关系。
- Background → initial_life_context.current_role，其他初始地点/阶段可通过高级 YAML 按现 schema 编辑；不重写已有 runtime life。
- Interests / dislikes → seed_preferences。

普通表单按原始字典局部更新，保留未显示字段。Raw YAML 用 safe_load + 原 Pydantic schema，并禁止删除原有字典键。
当前 schema `extra=forbid`：未知字段导致拒绝保存，原文件保留，不能为了兼容而静默丢弃或放宽 schema。
失败不覆盖原文件；保存前私有备份 + 同目录原子 replace；revision 冲突需刷新，不自动合并。
应用配置和角色是两个明确的保存动作，不是跨文件事务；一个保存失败不会回滚另一个已经成功的保存。
生效需要重启，现有 Memory/Identity/运行 DB 不迁移。个性化后的 seed 和私有备份不应提交 Git。

## 安全与连接测试

- 只绑定 127.0.0.1 随机空闲端口。每次启动生成随机 bearer token，放 URL fragment，不进入 HTTP URL/access log。
- 所有 Console 页面、数据与操作 API 认证；未认证页面只返回 401 登录壳，不含配置。无 CORS，检查 Origin/Host，cookie POST 必须同源；业务写请求 JSON-only、限额、读取超时。公开资源仅固定 CSS/JS，不公开模板。
- CSP self-only、no-store、no-referrer、frame deny；Jinja autoescape，JS 只用 value/textContent。没有内联脚本或外部 CDN。
- 现有密钥只返回“已配置/未配置”；保留/替换/清空明确区分，空替换和 masked placeholder 被拒绝。
- 错误仅安全类别/类型，不回传 raw stderr、Pydantic input、HTTP body/header、URL 或 traceback。
- API 地址是**可信操作者的网络权限**，不是面向不可信公众的 URL proxy。仅手动测试时发送密钥；支持自建 HTTP API，公网部署请用 HTTPS。不要把 token 分享给不可信用户。
- 无 Web 访问日志；Manager 原日志仍受 64 KiB/100 行和 redact 约束。历史无标记秘密无法保证识别，日志仍是私人数据。
- 同服务保存串行且校验原文件，原子替换前再次检测外部编辑；不声称能锁住任意外部编辑器，避免多个配置工具并行写。

LLM 测试使用原 adapter，禁重试、10 秒请求超时、16 tokens；Memory 用原 API adapters，单条 embedding / rerank。
OneBot 使用 v11 `get_status` + `get_login_info`，校验响应、online/good、机器人 ID；不发送聊天、不登录账号。
只 WS 可连不能算 QQ online；即便 online 也不能证明 allowlist 私聊收发成功。
Manager 状态不会缓存测试为永久 Ready：配置有效 ≠ API 可用 ≠ Core 初始化成功 ≠ QQ 已就绪。

## 复用决策

Direct Reuse：Starlette（BSD-3-Clause）、Uvicorn（BSD-3-Clause）、Jinja2（BSD-3-Clause）。
它们是成熟维护中的轻量 ASGI/模板组件，通过依赖引入、不复制外部源码；许可证随包分发。
[Starlette](https://github.com/Kludex/starlette)、[Uvicorn](https://www.uvicorn.org/)、[Jinja](https://jinja.palletsprojects.com/en/stable/api/)。
FastAPI 可行但本任务不需 OpenAPI/第二套请求模型；stdlib HTTP 会增加安全和生命周期手写量，未选择。
无 React/Node build/配置框架；未来替换 UI 不改变 env/YAML/Native 控制。
OneBot 动作参考[官方 v11 API](https://github.com/botuniverse/onebot-11/blob/master/api/public.md)。

## 验证与保留限制

自动测试覆盖临时文件、secret roundtrip、并发拒绝、身份保护、schema/非法 YAML、鉴权、Host/Origin、
安全渲染、原子保存失败、连接测试、安全错误、Web graceful drain、Manager 生命周期。
集成测试使用 fake 外部 LLM/传输和真实 Core/Conversation/SQLite，覆盖 Web 保存 → Manager fresh Start → none/qq、归档与 send_private_msg。
`scripts/run_web_setup_smoke.py` 启动真实 loopback Console，认证后打开全部八页，执行离线保存/本地 runtime check 并关闭；另外运行 Manager/Setup headless smoke。

Docker 为 P2：保留原 Dockerfile/Compose，只把新静态资源加入 build allowlist，不增加 Docker controller 或另一套配置。
当前环境没有 Docker 实机验收；不解决 Docker Hub 可达性，不声称 image 已 build 成功。

用户已报告 Ubuntu 安装、Manager、API 测试、保存/检查、Start/Stop/Restart 和退出 Manager 后 Core 常驻通过；不是本轮重新执行的证据。仍需真实 Ubuntu 验收 Console 的多页导航 / 会话 / 窄屏，及 SnowLuma 登录后用 allowlist QQ 发一次私聊，确认回复与日志。
Linux pidfd/flock 进程控制仍沿用原限制；Windows 只验证 Web/TUI/fake runtime。无开机自启、supervisor、update、backup UI、自动安装 OneBot 或 Phase C。
