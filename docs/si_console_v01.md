# SI Console v0.1

长期使用的本地管理后台，不是聊天客户端。白 / 暖灰为主，低饱和粉色强调；左侧唯一 SI initials 占位头像，不引入角色立绘。

## 入口与页面

`bash install.sh` → `si` → 配置 → SSH tunnel → 打开完整临时 URL → SI Console。

| 路由 | 当前能力 |
| --- | --- |
| `/` | 实际 Manager 状态、数据库大小、UTC 今日归档消息数、全部记忆数、最近记忆写入时间，以及紧凑的情绪/心情/主要对象关系摘要；无 API / Token / 时长统计 |
| `/chat` | none/qq、OneBot、Bot QQ、token、私聊 allowlist 条目编辑、现有连接测试；群聊未启用 |
| `/media` | 明确的空状态与禁用上传占位；无存储、识别、自动发送或其他媒体管线 |
| `/character` | 既有 schema 字段与高级 YAML；除 working name 外身份字段锁定 |
| `/affective` | 只读主要对象关系五维、当前心情四维、最近情绪、折叠来源/原始值；手动刷新 |
| `/memory` | 只读内容搜索、分页卡片、类型/状态/来源/重要度/时间、证据引用和 embedding 缓存描述 |
| `/models` | 原 LLM / Embedding / Reranker 配置、密钥保留/替换/清空、连接测试 |
| `/runtime` | 原 Native start/stop/restart、状态、本地 health、版本、bounded/redacted 日志刷新 |
| `/system` | 路径/DB 主文件大小/备份目录存在性/版本/配置/seed 文件状态；无恢复或破坏性操作 |

备份目录存在不代表备份完整；缓存存在不代表与当前 provider/model 一致。QQ 默认未验证；只有手动 OneBot probe 查询 online/good/login info，结果也不能替代真实私聊验收。消息统计包含 user/assistant archive，按 UTC 日界线，不等同发送成功次数。

## 复用与数据边界

继续直接复用现有 Starlette / Uvicorn / Jinja2，沿用上一版许可证和依赖，不引入新框架或 npm 构建。Jinja base/sidebar/forms + 九页模板、集中 CSS、原生 JS。桌面固定导航，笔记本窄侧栏，手机可展开堆叠菜单。

WebSetupServer / WebSetupService 名称保留作内部兼容入口；ConsoleService 只负责呈现和只读查询。Manager 创建 WebSetupService 时传入自身：Web 运行按钮 → **同一个 ManagerService** → 原 RuntimeController。无第二套 Core 初始化、进程管理、配置存储或状态缓存。

记忆查询使用标准库 sqlite3 `mode=ro`、`query_only`、只读事务、参数绑定；不构造会初始化 schema 的 SQLiteStore。每页 20 条，证据最多 51 条、缓存描述最多 21 条；不读取 embedding BLOB、不改 recalled 时间、不触发 extraction/retrieval。查询有步数上限，过大或损坏 DB 返回安全错误。未知数据库 schema 不迁移。Memory 卡片的类型来自原 memory_type，不捏造 preference 类型。

### 情绪与关系只读视图

首页增加一张“角色当前状态”摘要卡；详情页展示主要对象的关系阶段/五维与“非恋爱关系”边界，
角色自身 Mood 四维和最近最多 12 条 Emotion。保留现有白色/暖灰/低饱和粉色样式，只有侧栏原头像。

`ConsoleService → AffectiveConsoleService → mode=ro SQLite`，按 Seed Internal UUID 和数据库中
已持久化的 primary target 查询。不从 allowlist 顺序重新猜测关系对象，不初始化缺失关系。
不构造会初始化表或关系的 AffectiveStore；纯计算直接复用 Mood.recovered() / EmotionEvent.strength_at()，
只在内存中计算读取时状态，不写回 recovery、时间锚点或 decay。查询在同一只读事务内完成，连接显式关闭。

Mood 原值范围是 −1 至 +1，条形位置为 `(value + 1) * 50`，中线是 0，小字保留带符号数值；
该归一化位置不代表“正数心情百分比”。中文摘要使用确定性阈值，不调用 LLM。
Emotion 强度显示当前衰减值，初始强度/decay_until/source_event_id 在默认折叠来源信息中；
长原因摘要安全截断并可展开。首页仅显示最显著的最多三种活跃情绪。
Core 可能清理过期记录，详情页只展示当前存储中保留的近期事件，不宣称完整历史。

`GET /api/affective` 需要原认证和同源边界；POST/PUT/PATCH/DELETE 不允许更新状态。
没有编辑、重置、清空、滑块或自动轮询。无数据库、无表、无关系、无心情、无情绪分别显示空状态；
读取/校验失败显示“状态读取失败”，日志仅记录异常类型，其他页面仍能打开。
来源摘要使用原 redaction 和 DOM textContent，不读取原始聊天、完整 Prompt 或 Appraisal。
调试信息仅包含原始数值、UTC 时间、活跃数及已有 primary target 假名 UUID。

应用页面只提交本页字段，共用整个 env 文件 revision，跨页面/外部修改会拒绝 stale 保存。原子替换、私有备份、OS env 优先和启动 fresh reload 保持；保存不会自动重启。Character 保存路径不改，UUID / development / formal identity / stage / continuity 继续锁定。

## 认证与安全

- 默认仅 127.0.0.1 随机端口；每次服务独立临时 token。完整入口 URL 的 fragment 不发送到 HTTP，JS 立即清除地址栏 fragment，再以 Bearer 请求换取随机 HttpOnly / SameSite=Strict 会话 cookie。cookie 名每实例随机，值不等于入口 token；只在此服务器生命周期有效，不使用 localStorage。
- 全部九页需要认证；无凭证时只返回 401 的静态登录壳，不含角色配置。刷新/导航复用会话。直接 Bearer 仍兼容测试客户端。
- cookie 写请求强制完全匹配 Origin（含端口）；Host 只允许 loopback，拒绝跨站 Origin。API body 限额/超时、安全异常和显式 JSON 写操作保留。
- 仅公开固定 CSS/JS，不暴露 Jinja 源模板；CSP self、frame deny、no-referrer、no-store，Jinja autoescape，DOM 只用 textContent/value 创建节点，不执行配置或记忆 HTML。
- 密钥不回显。数据 / 日志通过既有 redaction；不是通用秘密发现器，未标记历史秘密与私人记忆仍为敏感信息，只向可信操作者展示。HTTP cookie 不设 Secure 是因为仅允许本机 HTTP + SSH tunnel；不可直接暴露公网。
- 不记录 Web access logs。退出 Manager 等待 Web 请求完成并关闭 Console，不调用 Core stop。Core 只有显式停止/重启才受影响。

## 验证与限制

`pytest` 覆盖九路由、导航/assets、认证、cookie CSRF、密钥处理、身份保护、旧配置测试、只读搜索/分页/缓存证据、runtime 委托、fresh config、安全错误及原 TUI。新增状态读取、衰减/恢复只在内存计算、负值归一化、空/error 状态、GET-only 与数据库字节不变测试；本机有 Node 时执行原生 JS 渲染器检查，不要求安装浏览器或 npm 依赖。JS 另做语法检查。

`python scripts/run_web_setup_smoke.py` 保留命令名，执行真实 loopback 的九页 Console / Web Setup smoke。
先验证空状态不创建 DB，再显式创建临时合成 DB，验证首页/详情数据读取与写操作拒绝、DB 字节不变；
退出时删除临时目录。不调用真实 API、不访问真实 runtime DB。

本轮回归：598 passed、3 skipped；Ruff、format、pip check、JS syntax 检查通过。
Console/Web Setup、Manager、Setup smoke 通过。Git index/HEAD 在本轮开始前已损坏，
标准 git status / git diff --check 无法执行；按修改前源码副本进行独立差异检查，未修复或覆盖 Git 元数据。

用户已报告旧版 Ubuntu 安装 / TUI / Web Setup / 三类 API 测试 / 配置检查 / Core 启停 / Manager 退出保留 Core 通过，SnowLuma 已部署。**这些不是本轮 Console 的实机验证**。本轮仍需 Linux SSH tunnel + 浏览器多页/会话/窄屏验收、Web 启停及与 TUI 同时操作验证；真实 QQ allowlist 私聊尚未最终验收。Docker 未新增能力，未声明 image/Compose 已验收。

没有自动轮询、事件流、在线时长/API/token 计数、群聊、记忆编辑、图片识别、媒体数据结构、Web 备份恢复、更新或 Phase C。本轮只保留明确的 UI/route 边界。
