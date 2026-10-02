# A6.1 — SiliconFlow Qwen3 Retrieval Benchmark Evidence

## 1. Benchmark setup

使用 A6 现有 API adapters 和生产 MemoryRetriever / MemoryReranker；临时 SQLite，仅合成 fixture，raw query，无额外 instruction、threshold 或 rerank bonus。每 profile fail-fast、零 retry。

2026-10-02 UTC 已完成真实 provider smoke、API-only benchmark，以及显式 `--with-local-baseline` 对照；均正常退出。第一次 benchmark 启动因沙箱临时目录权限失败，发生在 SQLite 初始化、任何 benchmark API request 之前；授权重跑，不属于 API retry。
API-only 明细：`runtime/benchmarks/a6_1_20261002T030325316791Z.json` / `.md`；对照明细：`runtime/benchmarks/a6_1_20261002T030640287205Z.json` / `.md`。这些合成实验结果被 Git ignore，不包含 key。以下主要采用同一次对照运行的结果，不混合两次 latency。
Fixture SHA256：`39efb1d34e8c377017431ae3f958319a8c42f85b1108600f71398ff45048b424`。

## 2. Models

- API：SiliconFlow `Qwen/Qwen3-Embedding-8B` → `Qwen/Qwen3-Reranker-8B`。
- 明确期望 embedding dimension 配置为 4096；运行时必须由真实 response 校验，不以模型名猜测成功。
- 可选 baseline：CPU `BAAI/bge-base-zh-v1.5`（768）→ `BAAI/bge-reranker-base`，只在 `--with-local-baseline` 时执行。

真实 smoke：embedding shape `(1, 4096)`、float32；3 条 rerank scores 按输入对齐，电脑购买 query 将 Mac 排第 1（0.997101），咖啡第 2（0.005583），自行车第 3（0.000294）。这些分数不是概率或通用门槛。

## 3. Dataset size

复用未修改的 `tests/fixtures/memory_retrieval_cases.json`：36 memories、18 queries，16 条有 expected，2 条 no-related。保留 dangerous_memory_ids 等原有标签。不新增/改写 case。

## 4. Metrics

Recall@1/@3/@5 和 MRR 使用现有 calculate_metrics；no-related 不计入分母，MRR 有 semantic Top-10 边界。Recall 是每 query 找回 expected IDs 比例的平均，而非“命中任意一条”的比例。

| Pipeline | Recall@1 | Recall@3 | Recall@5 | MRR |
| --- | ---: | ---: | ---: | ---: |
| Qwen3 semantic | 0.802083 | 1.000000 | 1.000000 | 1.000000 |
| Qwen3 semantic → rerank | 0.802083 | 1.000000 | 1.000000 | 1.000000 |
| BGE semantic | 0.802083 | 0.968750 | 0.968750 | 1.000000 |
| BGE semantic → rerank | 0.802083 | 0.968750 | 1.000000 | 1.000000 |

独立 API-only 运行的四项指标相同；两次 API cosine / rerank 分数存在小幅变化，不能假设服务逐位确定性。

## 5. Latency

API 计 HTTP 请求（连接、完整 body read）；local 计 encode/score 调用且含首次 model load。单位为秒，p50/p95 使用线性百分位数。单次计时不证明稳态吞吐或 SLA；请求批量大小与首次加载也不相同，不能只比较均值宣称速度优劣。

| Component | Requests / calls | Average | p50 | p95 |
| --- | ---: | ---: | ---: | ---: |
| Qwen3 embedding HTTP | 20 | 3.103587 | 1.623275 | 7.970008 |
| Qwen3 reranker HTTP | 18 | 0.692690 | 0.463542 | 1.818454 |
| BGE embedding CPU calls | 19 | 2.239315 | 0.019726 | 4.238326 |
| BGE reranker CPU calls | 18 | 0.470569 | 0.113249 | 1.089879 |

API-only 单独运行：embedding average/p50/p95 = 2.435581/0.868545/9.222148；reranker = 0.462969/0.468811/0.484509。显示网络计时存在明显运行间变化。

以上是 A6.1 历史数据，不追溯改写。A6.2 审查确认当时每请求新建 HTTP client，计时 transport 也重建池，未区分首/热请求。A6.2 改为 provider/transport 生命周期复用，并增加 `--latency-profile`、first/warm 分组及 production Need Gate E2E 计时；后续证据见 [A6.2 Deployment / Latency Review](../a6_2_api_deployment_profile.md)。不能把不同时间与网络条件的运行直接解释为 pooling 的因果加速倍数。

A6.2 真实运行已完成：embedding first=1.398524s、warm avg/p50/p95=1.884223/0.294326/7.906770s；rerank first=1.421195s、warm=0.298292/0.263338/0.497133s。Need=true E2E avg/p50/p95=2.272002/0.564229/7.540238s（5 次）；Need=false 无 embedding/rerank。48/48 API 请求成功，仍存在尾延迟。新证据明细与 payload/样本限制统一记录在 A6.2 文档，不替换本节历史表格。

## 6. Token usage / Cost

仅报告 API 实际 usage；缺失保持 null。未配置可靠价格时不估算费用。需要明确价格及全请求 input usage 才估算 input-token cost。

每次完整 API benchmark：embedding 返回 700 prompt/input tokens、700 total tokens（20/20 requests 有 usage）；reranker 未返回可用 usage，保持 null（0/18）。真实 smoke 另返回 embedding 6 tokens，reranker usage 仍缺失。两次完整 benchmark + smoke 共报告 embedding 1406 tokens；这不是含 reranker 的完整计费总量。
本轮未配置价格/币种，estimated cost 为 null；不据此推算 reranker 费用或服务账单。

## 7. Failure count

两个完整 API benchmark 各 38/38 请求成功，API failure count = 0；smoke 2/2 成功；baseline 37/37 local calls 成功。真实运行未观察到 HTTP error、timeout、rate limit、server error 或 malformed response；这些失败路径由 offline MockTransport 测试覆盖，不表示已真实验证服务故障恢复。

## 8. Observations

结果由脚本写入 Git 忽略的 runtime/benchmarks，含 JSON 明细和 Markdown summary；不输出 key/header/HTTP secrets。小规模合成 fixture 不证明自然语言泛化、所有 API 场景兼容或真实 Memory 召回质量。

- Qwen3 的 Top-3 在此 fixture 略优：`q_pinn_training` 的 `m_pinn_data` 在 Qwen3 排第 2，BGE rerank 排第 4；这是 Recall@3 差异的来源。
- 两者 Recall@1/@5、MRR 相同；多个 queries 含多条 expected，因此 MRR = 1 并不表示所有相关 Memory 都排第 1。
- 无相关 query 仍会返回候选：Mars semantic Top-1 cosine，Qwen3 0.380941 / BGE 0.377360；Soup，Qwen3 0.510259 / BGE 0.322965。未由此添加 threshold，也不能跨模型比较 score 的绝对标尺。
- CPU BGE 的热调用 p50 较低，Qwen3 API 提供无需本地模型的运行方式；本轮不据单次 latency 或缺失成本判定部署优劣。
- 正常退出已清理临时 SQLite，未读取或写入真实 `runtime/si_001.db`；原 fixture 与生产默认均未修改。

## 9. Decision status

COMPARABLE

这是对本轮完整对照的人工证据审阅标签：整体指标接近，Qwen3 Top-3 有一例改善，不是泛化优势证明。runner 自动生成报告仍默认 INCONCLUSIVE，等待人工审阅，不实现自动 winner 判定。

生产默认保持 local/local；不得仅凭 mock 结果或单次小 fixture 自动切换 CLI/QQ/deployment。
