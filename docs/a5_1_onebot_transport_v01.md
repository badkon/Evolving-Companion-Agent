# A5.1 — SnowLuma / OneBot WebSocket Transport v0.1

状态：实现并通过本机 Fake OneBot WebSocket 验证；没有登录 QQ、启动 SnowLuma 或验证真实账号投递。

## 1. Architecture

```text
SnowLuma Universal OneBot WS Server
↕ JSON event / action / response
OneBotWebSocketTransport
↓ serial asyncio.to_thread
QQPrivateChatAdapter
↓ send(text)
existing synchronous Conversation Core
```

`qq_transport.py` 只负责连接、帧分类、发送、echo correlation 和生命周期。`qq_cli.py` 是入口 wiring，按既有 YAML → Projection → Conversation 链初始化 World / State / Life / Memory 服务；Adapter / Core 本身没有修改。不启动本地 CLI 的 developer command handler。

### Reuse Evaluation

直接复用 [websockets asyncio client](https://websockets.readthedocs.io/en/latest/reference/asyncio/client.html)，新增 `websockets>=15,<18`。它提供成熟的异步 WS client/server、handshake、关闭与协议处理；[BSD license](https://websockets.readthedocs.io/en/stable/project/license.html)允许依赖使用，无复制外部源码。相较完整 bot framework，这个协议库不规定 Character / DB / Prompt 架构；可通过重写这一 transport 替换，不迁移 Character 数据。只连接配置的 endpoint，fake 验证只访问 loopback；不需要 stdlib 自写 WebSocket。

[SnowLuma SDK](https://snowluma.github.io/en/docs/sdk)是 TypeScript / Node 客户端，适合作为协议参考，但引入额外语言运行时不符合当前 Python 最小边界，未安装或复制 SDK。SnowLuma 是独立部署的协议层，不作为本项目嵌入式 Character framework。

## 2. WebSocket Connection

正向 client 连接配置 URL；默认开发 endpoint `ws://127.0.0.1:3001/` 来自 [SnowLuma 官方配置](https://snowluma.github.io/en/docs/guide/configuration)，不是不可变地址。要求服务端为 `Universal`，同连接接收 event 和 action response；不是 reverse WS server，也不自动猜测 `/api` / `/event` 路径。

库 API 使用 `additional_headers`；可选 token 以 `Authorization: Bearer ...` 发送，依据 [SnowLuma 连接鉴权说明](https://snowluma.github.io/en/docs/guide/connect-bot)。不把 token 拼进 URL，拒绝带 query 或 userinfo 的 endpoint。显式禁用环境代理，避免本机连接意外经代理路由；远程连接可配置 wss URL。

## 3. SnowLuma Boundary

SI 不负责 Linux QQ、注入、登录、WebUI、升级或 Docker；不读取 SnowLuma 的配置文件，只消费 endpoint。部署者负责启动服务及账号/token匹配。真实部署前必须核对服务端账号和 SI_QQ_BOT_USER_ID，本版没有独立账号自动发现或绑定校验。

## 4. Event Flow

接收 JSON text frame → 检查 object → 区分 API response → event 交原 Adapter。invalid JSON、binary、非 object 和缺少 post_type 的非 response 帧安全忽略。Transport 不复制 allowlist、friend/private、自身过滤或 dedup。

一次只 await 一个 Adapter 调用，`asyncio.to_thread` 防止同步 LLM 阻塞事件循环。Store 每次方法调用独立创建、使用并通过 closing 释放 SQLite connection，没有跨线程共享常驻 connection。State / Life 等服务只持有 Store / Clock，串行调用不引入并行 State 写入。

处理一轮期间暂停读取后续应用层帧，但 WebSocket 底层 I/O 与事件循环仍运行；缓冲使用库默认边界，不建立业务消息队列。这也意味着上一动作 ack 在后续长 turn 期间可能延迟处理；30 秒超时是保守 delivery 诊断，不证明消息绝对没到 QQ。

## 5. API Action Flow

仅 handled + response 发 `send_private_msg`：params 为整数 user_id、原样 Character text、`auto_escape=True`，加 UUID echo。不拼 CQ reply 元数据；角色内容即使含 CQ 样式字符串也按普通文本发送。其余 status 不发消息；failed 只记录 developer error。

这是 action 提交，不是再次生成回复。发送操作失败不会重新调用 Adapter / Core。

## 6. Echo Correlation

维护最小 `echo → timeout handle` pending map；记录最近一个 `DeliveryResult`。收到 echo / retcode / status 形状的 API response 都不会进入 Adapter，即使夹带 post_type。

只有匹配 pending echo 的 `status=ok` 且整数 retcode=0 判 sent。错误 / 无效 response 判 failed；错误 echo 不完成其他动作。超时、socket send failure、连接断开会完成为 failed，取消 timer 并移除 pending；迟到 ack 不复活已结束动作。没有持久化 action history、通用 RPC framework 或自动 delivery retry。

## 7. Delivery Semantics

**At-most-once Character processing 仅限同进程、同 Adapter、message_id 尚在其有界 cache 的范围**：重连不换 Adapter，所以同 ID replay 不再次进入 Core。沿用 A5 的 1000 条 FIFO cache；进程重启 / 淘汰后不能承诺全局 at-most-once。

不保证 exactly-once QQ delivery。Core 已成功生成、归档并更新 History，QQ action 仍可能失败或 ack 丢失；sent 只表示收到成功 API response，不是对方已读。不实现 outbox、retry DB、事务消息或 Archive 回滚。

## 8. Reconnect

默认固定间隔 5 秒，单次 run 总共最多 3 次额外连接尝试（成功重连不重置预算），初次连接失败或连接结束后按预算重试。预算耗尽正常结束并关闭。没有指数 backoff 或 background reconnect task。

close 设置停止标记、关闭 WS、失败并清理 pending。等待重连间隔时也能立即停止。重连沿用同一 Adapter/cache，无新的 Conversation / Character / UUID。

## 9. Failure Boundary / Graceful Shutdown

Adapter failure 不发系统错误台词；delivery failure 不撤销已完成 Core，不删除 Archive / History，不重跑 LLM。仅记录固定原因，不打印异常内容或服务端 wording。

Ctrl+C / SIGINT 通过 asyncio.run 取消主任务，finally 调用 close。已开始的 Core thread 不可安全强杀，因此使用 shield 并等待其完成后退出，不留孤立 turn 继续改数据；已主动关闭时不再发该回复。长 LLM 调用可能延迟退出，依赖现有 Core/provider 调用超时，不引入强制中断或新的 retry。

## 10. Configuration / Entry Point

`python -m evolving_companion.qq_transport`

入口使用既有 `.env.local` loader，环境变量优先，不在底层 import 时读 secrets。必需 DeepSeek key 及路由配置；入口没有启动 SnowLuma。配置：

| Variable | Meaning |
| --- | --- |
| DEEPSEEK_API_KEY | 既有真实 Core LLM 配置 |
| SI_ONEBOT_WS_URL | 可选，默认 ws://127.0.0.1:3001/ |
| SI_ONEBOT_ACCESS_TOKEN | 可选，仅 WS 握手；不传入 Character Core |
| SI_QQ_BOT_USER_ID | 必需，连接账号的外部 ID |
| SI_QQ_ALLOWED_USER_IDS | 必需，逗号分隔；空列表 fail-closed |

不把真实账号、token 写入 tracked config。入口使用项目根目录 `runtime/si_001.db`，只有人工启动真实入口才初始化它；tests/smoke 不运行这个真实初始化。配置最小使用 env，没有新增 settings framework 或 CLI override。

## 11. Security

应用 logging 覆盖 connect、connected、disconnected、reconnecting、ignored reason(debug)、adapter/send failure。无 URL、token、QQ 内容、Prompt、Memory dump 或原始 exception 输出。专用 WebSocket protocol logger 设 WARNING，避免 DEBUG 握手日志泄漏 Authorization；不要手动开启其 DEBUG 或记录完整 frame。

allowlist 是 Adapter policy，不是连接认证。默认本机 ws；远程私密流量应走 wss / 可信网络，不把 bearer token 暴露到公开 endpoint。

## 12. Known Limitations / Verification

无 persistent dedup、delivery outbox、guaranteed QQ delivery、group、media、多角色关系、HA、broker、业务队列或 UI。单 Character / 单进程 / 串行 Core 假设保持；原 F02–F06 backlog 未处理。

离线 `python scripts/run_onebot_transport_smoke.py` 启动本机随机端口 Fake Server，不使用 QQ、SnowLuma、DB、模型或 API key；验证动作内容 / echo / ack、重复不发、断连后重连与 replay dedup，再 graceful shutdown。pytest 使用相同 loopback 技术（不是外网），测试错误 echo、API failure、timeout、断连、Core failure、日志脱敏、重连预算、close 等待和任务取消，以及配置注入。

## 13. Real SnowLuma Next Step

下一任务才验证真实部署账号、Universal role、token、friend event 格式及实际发消息；本阶段没有这些 live evidence。SI 入口已经能连接配置 endpoint，但不能把 Fake Server 成功描述成真实 QQ smoke verified。
