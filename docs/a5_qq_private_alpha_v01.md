# A5 — QQ Private Chat Alpha Adapter Core v0.1

## 1. Purpose

建立最小、可离线测试的 QQ 私聊协议边界。不登录 QQ、不启动 SnowLuma、不建立 WebSocket 连接。本版是 Adapter Core 完成，不代表真实 QQ 私聊已上线。

## 2. Architecture

```text
QQ
↓
SnowLuma / OneBot Transport [future real transport]
↓ parsed event mapping
QQPrivateChatAdapter
↓ send(text)
existing synchronous Conversation Core
↓ assistant text
ExternalChatResponse → future transport send
```

实现集中在 `src/evolving_companion/qq_adapter.py`，没有多平台 registry 或 transport framework。输入、输出和处理结果采用标准库 frozen dataclass。`ConversationPort` 只包含现有 `send(user_message: str) -> str`，既支持实际 Conversation，也支持无 DB / LLM 的 fake。

### Reuse Evaluation

协议采用 [OneBot v11 官方 message event 定义](https://github.com/botuniverse/onebot-11/blob/master/event/message.md)作为 **Design Reference**：`private` 仍可能是临时群会话，需区分 `sub_type`。没有复制其实现代码。

评估了 [NoneBot OneBot adapter](https://github.com/nonebot/adapter-onebot)（MIT，公开维护的完整协议适配项目）。[官方安装与加载方式](https://onebot.adapters.nonebot.dev/docs/guide/installation/)需要 NoneBot driver / adapter 注册，覆盖网络和异步框架生命周期；成熟完整方案适合后续 transport 选型，本阶段只接受已解析 mapping，直接引入会增加依赖和运行耦合。其网络 / access token 边界也不应进入 Character Core。故暂作 **Design Reference**，不安装、不复制代码、不改变项目 license。

本次采用 **Custom Implementation** 的窄边界与已有 Conversation 复用：无新增依赖、无网络或第三方登录、无私人数据跨服务传递。后续可换 transport 而不修改 Character 的 `send(text)`；届时重新评估成熟 transport，不认为本次自定义 parser 是完整 OneBot SDK。

## 3. Character Core / Transport Boundary

Character Core ≠ Transport ≠ QQ ≠ SnowLuma ≠ OneBot。

Core 只接收文本；QQ sender/message ID、事件时间、group_id 和完整 JSON 不传入 Prompt、Character Seed 或 Conversation API。外部 `occurred_at` 只是 adapter 数据，不覆盖 Character Clock 或 State anchors。Adapter 不访问 Store，不改 Memory / State / World，不构造 Character Prompt，不调用 CLI 的开发命令 handler。

例如 QQ 文本 `/state energy low` 是普通聊天文本，不会获得本地 CLI 的开发权限。允许名单仅是入口授权，不意味着角色自动服从、信任或与该 QQ 号建立长期关系。

## 4. Supported Event

`handle_event(event)` 接受已解析对象，仅处理 mapping 中：

- `post_type = message`、`message_type = private`、`sub_type = friend`；
- `user_id` 为正整数或十进制字符串，规范化为字符串；
- `message_id` 为整数或十进制字符串（允许协议的有符号整数），规范化后用于去重；bool / float / 容器均拒绝；
- 非空字符串 `raw_message`，缺失时使用简化输入的 `text`；存在但无效的 raw_message 不用 text 绕过检查；
- 可选 `time` 为非负整数 Unix seconds，转换为 aware UTC；缺失 / None 时 `occurred_at = None`，不伪造消息时间。无法表示的时间拒绝。

保留输入文本，不改写人格或自动 strip 非空文本。CQ 段字符串（含 `[CQ:`）保守忽略；不解码 CQ、图片、语音或 message segment 数组，也不使用 `message` 字段作为第三种输入。未来 transport 负责提供本版支持的纯文本形式，不能静默丢弃媒体后假装收到纯文本。

ExternalChatMessage 字段：platform、conversation_type、sender_id、message_id、text、occurred_at。ExternalChatResponse 仅有 reply_to_message_id、recipient_id、text。dataclass 是 parser 的内部规范化结果，不提供绕过 event validation 的直接处理入口。

## 5. Private-chat-only Policy

群聊、`private/sub_type=group` 临时会话、other、缺少子类型、notice、request、message_sent 和其他 unsupported event 均 ignored。严格要求 friend 避免无法判断来源时默认为安全私聊；这是最小兼容子集，不宣称覆盖全部 OneBot v11。

## 6. Allowlist

构造函数显式传入 `allowed_user_ids`，为空时拒绝所有外部用户。非 allowlist 返回 unauthorized，无 response，不调用 Conversation，因此不 Archive、不触发 formation。没有把真实 QQ 号写入生产代码或 Character Seed。

## 7. Self-message Protection

必须传入 `bot_user_id`。在 allowlist / dedup / Core 前检查 sender 与它是否相同；即使 bot 被加入 allowlist 也 ignored。未来 transport 应保证配置账号和连接账号一致，当前 mapping parser 不是 transport 身份认证器。

## 8. Deduplication

每个 Adapter 实例保存最近 1000 个已尝试的规范化 message_id，可通过正整数 `dedup_capacity` 调整。FIFO 淘汰，重复不会刷新顺序。验证和安全过滤通过后、调用 Core 前登记 ID。

Core 失败也保留该 ID：失败可能发生在 user Archive 后，重复送达不能自动再执行一次。重复返回 duplicate、无 response；没有 retry queue / 自动重跑 / 缓存回复重发。

仅同实例且未淘汰的 ID 保证去重，重启 / 淘汰后可能再处理。同一实例绑定一个 bot、一个 Core；不是多账号 ID 命名空间。不要把有限内存 cache 描述为跨进程 exactly-once。

## 9. Failure Boundary

AdapterHandlingResult 的 status：handled / ignored / duplicate / unauthorized / failed；附可空 response 和固定 reason。

无效输入、unsupported 和过滤结果不进入 Core。Core 抛 Exception 则 failed / conversation_failed，response 为 None，不输出 provider 异常详情、不生成“发送失败”等角色台词、不撤销 Core 已提交的 Archive 或 State。Core 成功返回的回复原样透传，不增加系统错误前后缀。

真实发送尚未实现，因此 handled 只表示 Core 返回成功，不保证 QQ 投递成功。未来发送失败应在 transport 层单独报告，不再次调用 Conversation；Core 已完成的连续性不能依赖 QQ send 成功。已修复 F01 的完成边界保持不变；F02–F06 不在本任务处理。

当前不添加日志或记录完整事件。reason 可供 future transport 写最小 developer log；QQ ID 是外部 identifier，不是 Character internal_id，不应在通用日志中泄漏私人文本、Prompt 或 Memory dump。

## 10. Configuration

用最小运行时构造参数配置，不增加环境字段、CLI 网络入口或 settings framework：

```python
adapter = QQPrivateChatAdapter(
    conversation,
    allowed_user_ids=allowed_user_ids,
    bot_user_id=bot_user_id,
)
result = adapter.handle_event(parsed_event)
```

调用方提供已构造的 Conversation、允许的账号和 bot 账号；不在 Adapter 创建 production Store / Seed / LLM。bot token、QQ password 和未来 transport endpoint 不进入 Character Data。离线 smoke 的账号只是合成测试数字，不是真实账号配置。

## 11. Current Limitations

- 同步、逐轮调用；不是线程安全 / 并发 turn scheduler。未来异步 receive 必须串行调用 Core 或独立隔离，不全面 async 重构。
- 单 Character、单 bot、私聊测试边界，不创建各 QQ 用户的关系或独立 Character history。
- 无持久化 dedup、delivery acknowledgement、reconnect、heartbeat、真实认证、队列或 rate limiter。
- 不支持媒体、群聊、好友请求、自动治理、工具、Console 或 UI。
- 本版不对 QQ 事件的真实性作网络验证；allowlist 不能代替 authenticated transport。

离线验证：`python scripts/run_qq_adapter_smoke.py`。只用 FakeConversation，不需要 API key、数据库、模型权重、SnowLuma 或 QQ；检查 handled / duplicate / unauthorized / group / self 和 Core 只调用一次。pytest 还使用临时 SQLite + 实际 Conversation + fake LLM 验证协议不传入 Core、未授权事件无 Archive、adapter 不自行写 Memory / State / Life / World。

## 12. Next Stage: Real OneBot Transport

未来另行实现接收 / 鉴权、连接账号校验、消息串行调度与发送，并验证失败 / 重连语义。SnowLuma 不包含 Character logic；真实 OneBot action 构造属于 transport，当前 ExternalChatResponse 不是已执行的发送 API。

本阶段没有启动上述工作，没有真实 QQ 登录或外部消息发送。
