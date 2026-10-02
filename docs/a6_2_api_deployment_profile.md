# A6.2 — API Retrieval Deployment Profile + Latency Review

## 1. Local vs API deployment

code default 保持 local/local；部署建议与默认值分开，不自动切换。

| Profile | Embedding | Reranker | 用途 |
| --- | --- | --- | --- |
| Local Dev | local | local | CPU 本地开发、离线实验 |
| Server API | api | api | 推荐轻量服务器部署 |
| Hybrid | local/api | api/local | 显式组合，仍需本地依赖 |

这些只是现有环境变量的组合，不新增 profile manager、router 或部署向导。

## 2. Resource difference

API-only 安装 `python -m pip install -e ".[dev]"`，不用安装 torch、sentence-transformers、transformers，也不用下载 Hugging Face 模型缓存；核心仍使用 NumPy、HTTPX、SQLite 和既有 LLM SDK 等依赖。对旧环境不自动卸载已装软件。
本地使用 `python -m pip install -e ".[dev,local-memory]"`；extra 保留用于开发、benchmark 或未来人工选择的离线路径，本轮不实现自动 offline fallback。
资源收益是依赖/模型驻留减少，不是已测出的精确 RAM、磁盘或 CPU 节省。API 换来网络延迟、厂商依赖、费用及 query/Memory 文本外传。

## 3. Cold vs warm latency

A6.1 的 embedding avg/p50/p95 为 3.103587/1.623275/7.970008 秒；reranker 为 0.692690/0.463542/1.818454 秒。该历史运行每次新建 Client，计时 transport 也新建 HTTPTransport，无法保留 TCP/TLS 池，不作为 A6.2 热请求统计。

手动运行 `python scripts/run_siliconflow_retrieval_benchmark.py --latency-profile`。同一 fixture、同一 provider 生命周期，分别记录首请求与后续 warm avg/p50/p95；还测同一已热索引上的 5 次 production recall 和 1 次 Need=false。详细 JSON/Markdown 写入 Git 忽略的 runtime/benchmarks，不接真实 DB。
首请求指进程/Client 首次访问，不证明厂商模型冷启动；embedding 首请求为 32 条 memory 索引，第二批 4 条，其后是单 query，不能将异构 payload 的差异全归因于 pooling。E2E 包含 Need/Context、query 编码、SQLite/cache 读取、cosine、rerank 与 Top-3，不包含主 LLM、extraction 或 consolidation。warm 分位数仍是有限串行样本，不是 SLA。

2026-10-02 UTC 真实运行正常退出，结果：`runtime/benchmarks/a6_1_20261002T032738380245Z.json` / `.md`（Git ignored）；36 memories / 18 fixture queries，额外 5 次 true / 1 次 false。fixture SHA256 与 A6.1 相同，实际维度校验 4096，embedding 25/25、rerank 23/23 成功，无 retry。Recall@1/@3/@5/MRR 仍为 0.802083/1/1/1；不重跑 local baseline，不改写历史比较。

| API component | First request (s) | Warm count | Warm avg (s) | Warm p50 (s) | Warm p95 (s) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Embedding | 1.398524 | 24 | 1.884223 | 0.294326 | 7.906770 |
| Reranker | 1.421195 | 22 | 0.298292 | 0.263338 | 0.497133 |

Need=true E2E 5 次分别为 9.206901、0.564229、0.873586、0.347268、0.368029 秒；avg/p50/p95 = 2.272002/0.564229/7.540238 秒。每次 embedding/rerank 各 1，返回 3 条；已有 memory index 未重建。Need=false `你好` 为约 0.000014 秒，两种 API 调用均为 0，返回 0 条。
embedding 返回 735 input/total tokens；reranker usage 缺失保持 null，无价格配置不估算费用。热请求仍有明显长尾；同名服务模型与网络会变化，这不是与 A6.1 同时受控的 A/B 加速实验。
第一次沙箱启动因 Windows 临时目录权限在 DB 初始化失败，尚未发送 benchmark API 请求；授权后重跑成功，不属于 API retry。成功运行清理临时 DB，没有访问 runtime/si_001.db。

## 4. Server profile

在 Git 忽略的 `.env.local` 中配置（真实密钥只填本地，不提交；已有 OS 同名变量优先）：

```dotenv
SILICONFLOW_API_KEY=<仅本地填写>
SI_MEMORY_EMBEDDING_PROVIDER=api
SI_MEMORY_RERANKER_PROVIDER=api
SI_MEMORY_EMBEDDING_API_ID=siliconflow-cn
SI_MEMORY_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-8B
SI_MEMORY_EMBEDDING_DIMENSION=4096
SI_MEMORY_EMBEDDING_API_URL=https://api.siliconflow.cn/v1/embeddings
SI_MEMORY_EMBEDDING_API_KEY=${SILICONFLOW_API_KEY}
SI_MEMORY_RERANKER_MODEL=Qwen/Qwen3-Reranker-8B
SI_MEMORY_RERANKER_API_URL=https://api.siliconflow.cn/v1/rerank
SI_MEMORY_RERANKER_API_KEY=${SILICONFLOW_API_KEY}
SI_MEMORY_API_TIMEOUT=30
```

`${SILICONFLOW_API_KEY}` 使用已有 python-dotenv 展开，避免重复保存密钥；不是 shell 手动输入或新的解析器。generic API factory 不自动猜厂商或 URL，独立 `SI_MEMORY_*_API_KEY` 环境变量仍可覆盖；只设置 SILICONFLOW_API_KEY 而没有以上 production 配置不启用 API。主对话依旧另需 DEEPSEEK_API_KEY。
Local Dev 设置两个 PROVIDER=local，移除 API profile 的 MODEL 覆盖（或换回固定 BGE 型号）；Hybrid 仅保留对应 API 一侧配置。

## 5. Provider lifecycle

每个 API provider 懒创建并复用一个同步 HTTPX Client，沿用 timeout，使用其默认连接池；不引入 async HTTP、复杂 pool tuning、retry。benchmark 的 RecordingTransport 同样持有单个 HTTPTransport。
CLI 整个聊天循环、QQ 整个 transport run 共用启动时建立的 providers；entry ExitStack 注册关闭，即使后续初始化/运行失败也关闭。QQ 等待既有运行结束后释放；Conversation 不负责 HTTP 生命周期。独立构造 provider 的调用方必须 `try/finally: close()`；close 幂等，关闭后请求报安全错误。实验入口同样关闭连接，fake smoke 验证此边界。
方案 Direct Reuse 既有 [HTTPX Client / connection pooling / close](https://www.python-httpx.org/advanced/clients/)（BSD-3-Clause，成熟同步接口，零新增依赖），没有拷贝外部代码。HTTP keepalive 是否存活受默认空闲过期、服务端及网络影响，不声称每轮必然复用 TCP。

## 6. Cache semantics

memory_embeddings 是可重建索引；正常 query 仅补缺失/无效向量，再编码 query。新增 active memory、provider/model/dimension 不兼容、坏缓存或 explicit rebuild 才生成 memory 向量，不在每轮重新编码所有 active memories。
缓存 key 为 provider/model/dimension 描述符，旧空间可并存；不改变 SQLite schema 或权威 Memory/Evidence。没有 query cache。
生产 recall Need=false 在 retriever 之前返回，embedding/reranker 都不调用；Need=true 热索引为 1 次 query embedding + 1 次完整 Top-10 rerank → 至多 Top-3。独立成功回复后的 formation/consolidation 可能进行新 memory 编码/检索，不受本轮 recall Need Gate 限制，不能把 false 解读为整个 Conversation 永无 provider 请求。

## 7. Failure semantics

embedding/reranker API 错误由安全 MemoryProviderError 传播；Conversation 中 recall 在主 LLM 之前，无 best-effort 隔离，因此本轮回复中断，已写的 user Archive 保留。CLI 提示错误，QQ 保留既有失败处理。post-response formation/consolidation 沿原有容错边界，不追溯撤销已完成主回复。
这是明确的主回复可用性风险，记录为 future patch；本轮不改失败边界，不加自动 local fallback、重试或降级链。

## 8. Recommended deployment

**B. RECOMMEND API FOR SERVER**。服务器显式采用 api/api 可减少本地 ML 依赖和模型驻留；A6.1 小 fixture 的质量结论仍是 COMPARABLE，不是 API 全面胜出。code default 保留 local/local；不选择 C，不自动改变现有运行环境。
部署前确认账户额度、网络、费用、Memory/query 外传许可和数据保留政策；不要求交互输入密钥。

## 9. Known limitations

有限合成案例、单客户端串行计时不能证明自然语言泛化、并发吞吐、服务端冷启动或稳定 SLA。API 网络/排队/计算/传输等耗时未分别追踪；pooling 去掉可避免的重建成本，但无法保证解决尾延迟。没有自动 fallback、threshold、缓存清理、query cache、后台任务或新 Memory 算法。
本轮不运行生产 CLI/QQ，不读取真实 Memory，不修改 Character Data；pytest 只用 fake HTTP，无模型下载或真实 API。
