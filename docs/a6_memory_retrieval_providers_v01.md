# A6.0 — Memory Retrieval Providers v0.1

## 1. Provider Boundary

Memory ≠ Embedding Index ≠ Embedding Provider ≠ Reranker Provider。
`memories`、`memory_evidence`、`memory_supersessions` 仍是权威记录；`memory_embeddings` 只是可重建索引。

`EmbeddingProvider` 提供 `embed_texts(Sequence[str])` 和 `provider_id` / `model_id` / `dimension`，输出二维 normalized float32 NumPy array。
`RerankerProvider.score(query, texts)` 返回按输入顺序对齐的有限分数。`MemoryReranker` 按原始分数降序排序。

Memory → EmbeddingProvider → semantic Top-10 → RerankerProvider → max Top-3 injection。
Need Gate、Context Policy、active-only、无全局 threshold、无 salience/recency rank 均不改变。Character Seed / Projection / Prompt 和主 LLM Adapter 不改变。

## 2. Local BGE

`LocalBGEEmbeddingProvider` 保留 `BAAI/bge-base-zh-v1.5`、768 维、CPU、normalized float32、懒加载。旧实验入口的 `EmbeddingService` 名称和 `encode()` 保留兼容。
`LocalBGERerankerProvider` 保留 CPU `BAAI/bge-reranker-base`，懒加载 CrossEncoder，以 Identity activation 取得原始分数。当前生产 query 仍直接编码，不添加新 instruction。

## 3. API Providers / Reuse Evaluation

尚未选定最终 API 厂商；本版选择明确的 HTTP 格式，而不是宣称支持任意厂商：

- Embedding：POST 完整 endpoint，Bearer key，JSON `model` / `input: list[str]` / `encoding_format: float`；响应 `data: [{index, embedding}]`。按 index 恢复顺序，校验维数、有限非零向量并归一化。默认每批 32 条，query 单独一批。
- Rerank：POST 完整 endpoint，Bearer key，JSON `model` / `query` / `documents: list[str]` / `top_n`；响应 `results: [{index, relevance_score}]`。一次发送完整候选集（生产至多 10 条），要求每个输入 index 恰好返回一次，不跨批比较未必可比的分数。

HTTPX 作为现有 SDK 已使用的成熟通信库，新增直接依赖以明确边界。其 BSD-3-Clause、timeout / MockTransport 和轻量依赖适合此范围；Direct Reuse，不拷贝外部代码。[HTTPX 项目与许可证](https://github.com/encode/httpx)、[官方 transport 文档](https://www.python-httpx.org/advanced/transports/)。
Sentence Transformers 继续 Direct Reuse，仅移为 optional extra；Apache-2.0，成熟模型接口，但带来 torch/transformers 大型依赖，所以不作为 API-only 必需项。[项目与许可证](https://github.com/huggingface/sentence-transformers)、[CrossEncoder API](https://www.sbert.net/docs/package_reference/cross_encoder/model.html)。
API wire format 仅 Design Reference：[Jina embedding API 的兼容 JSON 格式说明](https://jina.ai/en-US/embeddings/)、[Cohere rerank 的 index / relevance_score 格式](https://docs.cohere.com/reference/rerank)。未引入厂商 SDK、框架、registry 或 router；若服务格式不兼容，应另行明确 adapter 需求，而不是猜测响应格式。

A6.2 起每个 API provider 首次请求懒创建一个 HTTPX Client，后续请求复用其连接池；入口用 ExitStack 注册 `close()`，正常退出和异常退出均释放。关闭幂等，关闭后不重新打开。默认 timeout 30 秒，无 retry / fallback / 自动厂商切换。HTTP、timeout、JSON、shape、index、非有限值错误返回安全 `MemoryProviderError`，不带密钥、URL 或响应正文，不记录 payload。endpoint 禁止 URL credentials / query / fragment；key 仅放 Authorization header。复用方案 Direct Reuse 现有 [HTTPX Client / pooling / close](https://www.python-httpx.org/advanced/clients/)，不新增依赖或拷贝外部代码；连接是否实际存活还取决于空闲超时、网络和服务端。
API 会向所配置服务发送检索 query 和 Memory content；隐私、数据保留政策与厂商条款须在真实部署前评估，Fake smoke 不验证这些边界。接口可替换，不将响应 metadata 写入 Character Data。

## 4. Index Compatibility

旧索引只有 `model_name` 不足以区分 provider。无需迁移表或权威数据：已有 TEXT `model_name` 现在保存 canonical JSON descriptor：

```text
["memory-index-v1", provider_id, model_id, dimension]
```

维度还保存在 `dimensions` 列；BLOB 仍为 float32。provider/model/dimension 任一变化都使用不同索引键，不复用旧向量；同 key 的损坏、维度不符、非归一化或非有限向量重新生成。旧纯模型名缓存保留但不会使用，升级首次按需重建。
不同空间的缓存可以并存。API 的 `SI_MEMORY_EMBEDDING_API_ID` 是不含 secret 的显式空间标识；不同 endpoint、模型 revision 或语义配置必须使用不同 ID/model，不能共用标签。服务静默改变同名模型时无法自动检测。

## 5. Rebuild Semantics

`MemoryRetriever.rebuild_memory_embeddings()` 显式重新编码全部 active memories，返回条数；正常 retrieval 只补当前空间缺失/无效索引。
切换 provider 后不需要删除 DB。生成和验证成功后批量写入索引；失败不会修改权威记录。旧空间索引不自动清理，不建立 migration framework 或后台任务。

## 6. Dependency Split

Core 保留 NumPy 并新增直接 HTTPX 依赖；`sentence-transformers` 移至 `local-memory` extra，torch/transformers 仅为该 extra 的传递依赖。

```bash
# API-only / offline tests (CI uses this)
python -m pip install -e ".[dev]"
# Local BGE development / existing local benchmark scripts
python -m pip install -e ".[dev,local-memory]"
```

API-only import/configuration/retrieval 不导入本地 ML stack；未安装 extra 且实际使用 local 时需要安装该 extra。默认 pytest 使用 fake，不下载模型、不访问外部网络。

## 7. Configuration

CLI 和 QQ 入口先按既有规则加载 `.env.local`（OS environment 优先），再显式调用 `create_memory_providers()`。底层模块 import 不读取 secrets 文件。

- `SI_MEMORY_EMBEDDING_PROVIDER` / `SI_MEMORY_RERANKER_PROVIDER`：`local` 或 `api`，默认 `local`。
- `SI_MEMORY_EMBEDDING_MODEL` / `SI_MEMORY_RERANKER_MODEL`：local 仅支持现有固定 BGE；api 必填。
- API embedding 必填：`SI_MEMORY_EMBEDDING_API_ID`、`SI_MEMORY_EMBEDDING_DIMENSION`、`SI_MEMORY_EMBEDDING_API_URL`（完整 endpoint）、`SI_MEMORY_EMBEDDING_API_KEY`。
- API rerank 必填：`SI_MEMORY_RERANKER_API_URL`（完整 endpoint）、`SI_MEMORY_RERANKER_API_KEY`。
- 可选：`SI_MEMORY_API_TIMEOUT`（秒，默认 30）、`SI_MEMORY_EMBEDDING_BATCH_SIZE`（默认 32）。

无厂商或 endpoint 默认值。key 不进入 YAML / World Seed / Prompt / 日志。既有实验脚本默认仍显式使用 local，不自动改成 API。`DEEPSEEK_API_KEY` 仍只用于主对话 / extraction / consolidation LLM，不替代 Memory provider keys。

## 8. Deployment Profiles

- Local Dev：local/local，需要 local-memory extra；当前兼容默认。
- Server API：api/api，A6.2 推荐服务器 profile；显式配置后不需要本地 ML stack。
- Hybrid：api/local 或 local/api；只要使用一个 local provider 就需要 local-memory extra。

这是配置组合，不是部署系统；无自动 profile 切换、Developer Console 或 deployment script。
完整 SiliconFlow `.env.local` 配置与延迟审查见 [A6.2 API Deployment Profile](a6_2_api_deployment_profile.md)。code default 仍为 local/local。

## 9. Smoke / Known Limitations

`python scripts/run_memory_provider_smoke.py` 默认全离线，用 HTTPX MockTransport 模拟 API，临时 SQLite 运行生产 Top-10 → rerank → Top-3；验证连续请求复用同一 client、Need=false 零请求、memory cache 不重建、切换 provider/model 重建、显式 rebuild、关闭连接和权威 Memory 不变，退出清理临时目录。不加载 `.env.local`、不接真实 runtime DB。

API embedding / reranker failure 仍沿既有 recall 边界中断主 Conversation；user Archive 已保留，主回复不执行。未擅自改成 best-effort。post-response formation/consolidation 仍沿既有独立容错边界。
分数仅用于同一候选集排序，不是概率、跨 provider 可比指标或全局相关性门槛。既有 `normalized_score` 保留 sigmoid 诊断兼容，不能解释为概率，排序只看 `raw_score`。
Fake 测试不证明真实厂商兼容性、语义质量或网络 SLA；A6.0 阶段仅验证 Fake，A6.1 的 SiliconFlow 有限真实验证见下一节，其他 API 厂商仍未验证。本版没有 threshold tuning、新 Memory 算法、去重、Memory v2、fallback 或部署框架。

## 10. A6.1 — SiliconFlow Qwen3 Benchmark

新增人工实验入口，直接复用 A6 `APIEmbeddingProvider` / `APIRerankerProvider`，无重复厂商 class，无生产 wiring/default 修改。
固定 endpoint 为 `https://api.siliconflow.cn/v1/embeddings` 与 `/rerank`，使用 `Qwen/Qwen3-Embedding-8B` / `Qwen/Qwen3-Reranker-8B`。
HTTP 格式参考 [SiliconFlow 官方 OpenAPI](https://github.com/siliconflow/siliconcloud/blob/main/openapi.yaml) 与 [rerank reference](https://siliconflow.readme.io/reference/creatererank)；模型与 4096 维原生配置参考 [Qwen 官方 model card](https://huggingface.co/Qwen/Qwen3-Embedding-8B)。这些资料不替代本账户真实 API 可用性检查。

在已有 Git 忽略的 `.env.local` 添加 `SILICONFLOW_API_KEY`；`.env.example` 只增加空变量。进程环境变量优先，无交互式密钥输入，缺失时退出并明确提示；不要求 DeepSeek key。本实验会发送既有合成 fixture 文本，不读取真实 Character Memory/Archive。

```bash
python scripts/run_siliconflow_provider_smoke.py
python scripts/run_siliconflow_retrieval_benchmark.py
python scripts/run_siliconflow_retrieval_benchmark.py --latency-profile
# 仅在已安装 local-memory 时显式运行本地 CPU baseline
python scripts/run_siliconflow_retrieval_benchmark.py --with-local-baseline
```

`--dimension` 为明确的期望 response dimension（默认 4096）；不会发送降维请求，也不会截断向量。每条 API response 由 A6 校验实际形状，smoke 打印实测 shape / dtype；不匹配则 fail-fast。`--timeout` 默认 30 秒，零自动重试。
原有 `memory_retrieval_cases.json` 完全不变：36 条 memories、18 条 queries（16 条有 expected、2 条 no-related），危险干扰项标签保留。raw query，无额外 instruction；不修改 Need/Context policy，但与已有 retrieval benchmark 一样直接逐条检索，不对 fixture 加生产 Need Gate。
临时 SQLite 运行生产 Retriever / Reranker，semantic Top-10 → rerank → Top-3；Top-5 指标仅评估候选排序，不将 injection 数量改为 5。

结果默认写入 Git 忽略的 `runtime/benchmarks/a6_1_<UTC timestamp>.json` 和 `.md`；可用 `--output-dir` 指定位置。输出保存 per-query Top-10、Top-3、expected IDs、cosine 与 raw score；no-related query 单独记录，不计入 Recall/MRR 分母。MRR 在 Top-10 边界内计算；Recall 为每 query 召回 expected IDs 比例的 macro average，不是 hit rate。
JSON 保存 fixture SHA256、safe request status、latency、usage。HTTP latency 包含连接与完整响应读取，不包含随后 NumPy parsing；local encode/score latency 包含首次懒加载，统计中的 local request count 表示 encode/score 调用而非网络请求。这些单次计时不能解释为稳态 SLA。
HTTP error / 429 rate limit / 5xx server error / timeout / malformed response 分别记录。某 profile 单次失败即停止该 profile，不重试；不完整 profile 的指标为 null，不用部分成功数据虚构总体分数。临时数据库正常退出即清理，不写 `runtime/si_001.db`。

只有显式指定 `--embedding-price-per-million` / `--reranker-price-per-million`（每百万 input tokens 单价）时才估算费用，可同时指定 `--currency CNY` 等标签；必须每个请求都返回 input usage，否则估算为 null。单价不进入核心 provider，未返回 usage 不按 0 补齐。默认不假设币种或价格。
报告 Decision 默认为 `INCONCLUSIVE`，不自动宣告 winner。人工评估结果与限制见 [A6.1 Benchmark Evidence](benchmarks/a6_1_siliconflow_qwen3_retrieval.md)。生产 local/local 默认继续保留，是否切换等待人工确认。

A6.2 的 `--latency-profile` 额外执行生产 Need Gate → query embedding → semantic Top-10 → rerank → Top-3：同一已热索引上 5 次 Need=true 与 1 次 Need=false，记录整体 elapsed 与 API 次数；断言每次 true 为 embedding/rerank 各 1、false 各 0。报告 first request 与后续 warm avg/p50/p95；first 指客户端首请求，不证明服务端模型冷启动，embedding 首批 32 条与后续 query 请求 payload 不同。计时 transport 自身也复用连接池，脚本退出关闭 providers。真实调用仅人工显式运行，不属于 pytest。
