# A7.2 — First-run Setup v0.1

## 1. Purpose

首次配置属于 Deployment / Configuration Layer，不是 Character Initialization。只为 A7 已部署实例配置凭据/providers/transport，绝不创建 Character、生成 UUID、清空 DB、重置 State/Memory/Life 或修改 World/Personality。无新的 setup_complete 数据库标记。

## 2. First-run Flow

完成 install、将 `/opt/si/app/.venv/bin` 加入 PATH 后：

```bash
si setup --help
si setup
# 配置完成后日常运维：
si
```

检测 Missing / Incomplete / Configured → 显示现有 Character 身份 → Configure/Reconfigure（LLM + Memory + Transport）→ Review → Confirm Save → 离线 deploy_check → 可选 Start → Summary / Finish。已完整配置时提示 Existing deployment configuration detected，提供 Review / Reconfigure / Exit，不自动覆盖。Review 现有配置不保存；退出/取消不写文件。Windows CLI 明确提示 Linux deployment；测试直接用 fake-path headless frontend，不访问 /opt/si。

## 3. Config Storage

仍为 `/opt/si/config/si.env`。`DeploymentEnvService` 提供 read/get_status/update/backup/atomic_write。复用已有 [python-dotenv](https://bbc2.github.io/python-dotenv/)（BSD-3-Clause）的 parser，保留未编辑 binding 和注释原文、处理空值/quoted values，不执行 eval/source/interpolation。对选中字段写入双引号单行值，兼容 systemd EnvironmentFile；编辑行会规范化格式及移除该行尾注释，独立注释/未知字段不受影响。缺失 profile 字段从已有 A7 template 补齐，已有 runtime path 与未知字段不覆盖。

旧文件写前创建 `si.env.bak.YYYYMMDD_HHMMSS`（UTC、独占创建、同秒冲突拒绝覆盖）。同目录临时文件写入、flush/fsync，然后 os.replace 原子替换；异常清理本次临时文件。Linux 在写 secret 前设置 si:si / 0600，Windows 不执行 Linux chmod/chown。config 目录必须由 A7 installer 建好；Setup 不创建 Linux 用户、systemd unit 或 /opt/si 目录。env 和备份均 Git ignore；无 backup retention。

## 4. Secret Handling

复用现有 [Textual Input(password=True)](https://textual.textualize.io/widgets/input/)。输入不 echo，已有凭据从不回填，仅 Configured/Missing，无前后片段/长度。默认 Keep existing；选择 Replace 才启用空 password input。新文件缺少 key 时需输入；不会将 OS 密钥未经确认复制到文件。API key 不作为命令行参数，不写日志、Review 或 Summary。结束/取消清空 frontend 输入和待写 payload；Python 字符串不是可保证清零的安全内存。

## 5. LLM

仅当前 DeepSeek API，配置 DEEPSEEK_API_KEY。不新增 model/base_url/timeout 变量、provider registry 或 fallback；当前 LLM Adapter 的已有配置保持不变。

## 6. Memory API

默认来自 A7 API-only template：SiliconFlow、API/API、Qwen/Qwen3-Embedding-8B / Qwen/Qwen3-Reranker-8B、SILICONFLOW_API_KEY。可编辑现有 model 字段；provider 固定 api/api，不提供 local/GPU、模型浏览器、threshold/Top-K/Need 算法设置。保留既有 generic key override/未知字段，无法通过 Setup 任意编辑它们。

## 7. Transport

仅 QQ OneBot / None。QQ 配置复用 SI_CHAT_TRANSPORT、SI_ONEBOT_WS_URL、SI_ONEBOT_ACCESS_TOKEN、SI_QQ_BOT_USER_ID、SI_QQ_ALLOWED_USER_IDS，access token 同样 masked/Keep/Replace，允许无 token。新文件默认 None；不暗中启用 QQ。None 可保存但 deploy_check 仍沿 A7 报 Transport ERROR，显示 Service cannot start until a transport is configured，无 Start 按钮。无 Matrix/Discord/Telegram 或 transport registry。

## 8. Review

保存前显示 providers/models、Transport、只读 Runtime DB 与 key 配置状态。Confirm/Back/Cancel；已有文件明确提示先备份。Runtime path 和 shortened identity 只读，不提供 Reset/New Character/Regenerate ID。配置模型/输入校验服务独立于 Textual。

## 9. Deploy Check

保存后自动在新的 Python 子进程调用已有 `evolving_companion.deploy_check --env-file <path>`。这是本地复用，不复制检查逻辑，也不调用网络。子进程仅继承 Manager 启动时的显式环境，让新文件被重新加载，避免长期 Manager 中旧 file-derived key 混入验证；argv 无 key，stdout 经过已知 key 屏蔽，stderr 不展示。
OS > env-file 的既有优先级不变；如环境显式覆盖编辑字段会提示。配置已保存与 deploy_check 通过分别记录，失败不会谎报 Ready；检测完整配置还须通过离线 check。Check 使用 A7 临时 writable probe/只读真实 DB，不初始化 runtime。

## 10. First Start

仅 Save 成功且 deploy_check 通过、非 None 才提供 Start SI service now。默认焦点 No/Finish；Yes 再检查并调用 A7.1 SystemdServiceManager.start，不另写 subprocess systemctl 或自动 sudo。权限失败提示 Configuration saved successfully / Service start failed，TUI 不 crash，之后可在 Manager 的 Service 页重试。Start 只证明 systemd 命令成功，不证明第三方在线或 Character 已回复。

## 11. Reconfiguration

Manager Overview 显示 Setup Required/Ready，并有 Run Setup；Configuration 页 Reconfigure 使用完全相同 SetupScreen/SetupService，无第二套 UI/env editor。保存后刷新 Manager 中由文件加载的环境、保留原显式 OS overrides；不自动重启已运行的 Core。外部修改文件时应关闭/重开 Manager，保存采用原文件内容比较，拒绝过时配置覆盖。

## 12. Security

白名单编辑、secret 输入无回填/日志、同目录 atomic replace、私有备份、拒绝 symlink/nonregular env、换行/控制字符注入、未知编辑字段。异常 UI/logs 仅安全提示与 exception type，from None 抑制底层含 private input 的异常文本。
当前操作者必须拥有 config 目录写权限，并能给文件设置 si owner；root Manager 或 si 用户是典型场景，Core 始终非 root。Setup 不绕过系统权限。备份含旧 secrets，按 config 一样保护。无需新框架或依赖，Textual、dotenv、stdlib Direct Reuse；自定义部分仅项目配置白名单/流程，可替换 frontend，不改变 Core。

## 13. Offline Semantics

没有 API connectivity test、QQ 连接或模型下载；明确 Connectivity tests are not part of setup v0.1。测试和 `python scripts/run_setup_smoke.py` 使用临时文件、fake secrets/check/systemd；验证 missing → input → review → save → check → skip start → exit，不污染真实 DB/Character。

## 14. Limitations

未实测 Linux ownership/systemd/polkit/SSH，仅 Windows 离线/headless 与权限 mocks。文件比较减少意外覆盖，但不是跨进程锁，维护者不得并发编辑；同秒 backup collision 明确拒绝，不 sleep/retry/覆盖旧备份。配置目录必须已安装；没有安装器替代、网络验证、任意 env editor、自动重启/更新/rollback 或 secret manager。
配置写入后 runtime 不变；用户明确 Start 后既有 Core 可按其正常规则初始化缺失记录，这不是 Setup 重置。长期文件保持敏感，本版不保证内存字符串的物理擦除。

## 15. Future Web Setup

UI-independent env/config/review/validation services 可供未来 frontend 复用；A7.3 Developer Console/Web Setup、remote admin、OAuth 等均未实现，无对应空模块。
