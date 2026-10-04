# SI Console v0.1

长期使用的本地管理后台，不是聊天客户端。白 / 暖灰为主，低饱和粉色强调；左侧唯一 SI initials 占位头像，不引入角色立绘。

## 入口与页面

`bash install.sh` → `si` → 配置 → SSH tunnel → 打开完整临时 URL → SI Console。

| 路由 | 当前能力 |
| --- | --- |
| `/` | 实际 Manager 状态、数据库大小、UTC 今日归档消息数、全部记忆数、最近记忆写入时间；无 API / Token / 时长统计 |
| `/chat` | none/qq、OneBot、Bot QQ、token、私聊 allowlist 条目编辑、现有连接测试；群聊未启用 |
| `/media` | 明确的空状态与禁用上传占位；无存储、识别、自动发送或其他媒体管线 |
| `/character` | 既有 schema 字段与高级 YAML；除 working name 外身份字段锁定 |
| `/memory` | 只读内容搜索、分页卡片、类型/状态/来源/重要度/时间、证据引用和 embedding 缓存描述 |
| `/models` | 原 LLM / Embedding / Reranker 配置、密钥保留/替换/清空、连接测试 |
| `/runtime` | 原 Native start/stop/restart、状态、本地 health、版本、bounded/redacted 日志刷新 |
| `/system` | 路径/DB 主文件大小/备份目录存在性/版本/配置/seed 文件状态；无恢复或破坏性操作 |

备份目录存在不代表备份完整；缓存存在不代表与当前 provider/model 一致。QQ 默认未验证；只有手动 OneBot probe 查询 online/good/login info，结果也不能替代真实私聊验收。消息统计包含 user/assistant archive，按 UTC 日界线，不等同发送成功次数。

## 复用与数据边界

继续直接复用现有 Starlette / Uvicorn / Jinja2，沿用上一版许可证和依赖，不引入新框架或 npm 构建。Jinja base/sidebar/forms + 八页模板、集中 CSS、原生 JS。桌面固定导航，笔记本窄侧栏，手机可展开堆叠菜单。

WebSetupServer / WebSetupService 名称保留作内部兼容入口；ConsoleService 只负责呈现和只读查询。Manager 创建 WebSetupService 时传入自身：Web 运行按钮 → **同一个 ManagerService** → 原 RuntimeController。无第二套 Core 初始化、进程管理、配置存储或状态缓存。

记忆查询使用标准库 sqlite3 `mode=ro`、`query_only`、只读事务、参数绑定；不构造会初始化 schema 的 SQLiteStore。每页 20 条，证据最多 51 条、缓存描述最多 21 条；不读取 embedding BLOB、不改 recalled 时间、不触发 extraction/retrieval。查询有步数上限，过大或损坏 DB 返回安全错误。未知数据库 schema 不迁移。Memory 卡片的类型来自原 memory_type，不捏造 preference 类型。

应用页面只提交本页字段，共用整个 env 文件 revision，跨页面/外部修改会拒绝 stale 保存。原子替换、私有备份、OS env 优先和启动 fresh reload 保持；保存不会自动重启。Character 保存路径不改，UUID / development / formal identity / stage / continuity 继续锁定。

## 认证与安全

- 默认仅 127.0.0.1 随机端口；每次服务独立临时 token。完整入口 URL 的 fragment 不发送到 HTTP，JS 立即清除地址栏 fragment，再以 Bearer 请求换取随机 HttpOnly / SameSite=Strict 会话 cookie。cookie 名每实例随机，值不等于入口 token；只在此服务器生命周期有效，不使用 localStorage。
- 全部八页需要认证；无凭证时只返回 401 的静态登录壳，不含角色配置。刷新/导航复用会话。直接 Bearer 仍兼容测试客户端。
- cookie 写请求强制完全匹配 Origin（含端口）；Host 只允许 loopback，拒绝跨站 Origin。API body 限额/超时、安全异常和显式 JSON 写操作保留。
- 仅公开固定 CSS/JS，不暴露 Jinja 源模板；CSP self、frame deny、no-referrer、no-store，Jinja autoescape，DOM 只用 textContent/value 创建节点，不执行配置或记忆 HTML。
- 密钥不回显。数据 / 日志通过既有 redaction；不是通用秘密发现器，未标记历史秘密与私人记忆仍为敏感信息，只向可信操作者展示。HTTP cookie 不设 Secure 是因为仅允许本机 HTTP + SSH tunnel；不可直接暴露公网。
- 不记录 Web access logs。退出 Manager 等待 Web 请求完成并关闭 Console，不调用 Core stop。Core 只有显式停止/重启才受影响。

## 验证与限制

`pytest` 覆盖八路由、导航/assets、认证、cookie CSRF、密钥处理、身份保护、旧配置测试、只读搜索/分页/缓存证据、runtime 委托、fresh config、安全错误及原 TUI。JS 另做语法检查。`python scripts/run_web_setup_smoke.py` 保留命令名，升级为真实 loopback 的八页 Console smoke，使用临时配置、不调用真实 API、不创建 runtime DB。

用户已报告旧版 Ubuntu 安装 / TUI / Web Setup / 三类 API 测试 / 配置检查 / Core 启停 / Manager 退出保留 Core 通过，SnowLuma 已部署。**这些不是本轮 Console 的实机验证**。本轮仍需 Linux SSH tunnel + 浏览器多页/会话/窄屏验收、Web 启停及与 TUI 同时操作验证；真实 QQ allowlist 私聊尚未最终验收。Docker 未新增能力，未声明 image/Compose 已验收。

没有自动轮询、事件流、在线时长/API/token 计数、群聊、记忆编辑、图片识别、媒体数据结构、Web 备份恢复、更新或 Phase C。本轮只保留明确的 UI/route 边界。
