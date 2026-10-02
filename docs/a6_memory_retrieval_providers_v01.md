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

每次请求显式关闭 HTTP client；默认 timeout 30 秒，无 retry / fallback / 自动厂商切换。HTTP、timeout、JSON、shape、index、非有限值错误返回安全 `MemoryProviderError`，不带密钥、URL 或响应正文，不记录 payload。endpoint 禁止 URL credentials / query / fragment；key 仅放 Authorization header。
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
- Server Lite：api/api，长期服务器目标；显式配置后不需要本地 ML stack。
- Hybrid：api/local 或 local/api；只要使用一个 local provider 就需要 local-memory extra。

这是配置组合，不是部署系统；无自动 profile 切换、Developer Console 或 deployment script。

## 9. Smoke / Known Limitations

`python scripts/run_memory_provider_smoke.py` 默认全离线，用 HTTPX MockTransport 模拟 API，临时 SQLite 运行生产 Top-10 → rerank → Top-3；验证复用、切换模型重建、显式 rebuild 和权威 Memory 不变，退出清理临时目录。不加载 `.env.local`、不接真实 runtime DB。

API embedding / reranker failure 仍沿既有 recall 边界中断主 Conversation；user Archive 已保留，主回复不执行。未擅自改成 best-effort。post-response formation/consolidation 仍沿既有独立容错边界。
分数仅用于同一候选集排序，不是概率、跨 provider 可比指标或全局相关性门槛。既有 `normalized_score` 保留 sigmoid 诊断兼容，不能解释为概率，排序只看 `raw_score`。
Fake 测试不证明真实厂商兼容性、语义质量或网络 SLA；真实厂商尚未验证。本版没有 threshold tuning、新 Memory 算法、去重、Memory v2、fallback 或部署框架。
