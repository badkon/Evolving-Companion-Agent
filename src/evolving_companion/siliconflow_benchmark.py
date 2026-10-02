"""Manual A6.1 experiment helpers; not wired into production entry points."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

import httpx
import numpy as np

from evolving_companion.memory_providers import (
    APIEmbeddingProvider,
    APIRerankerProvider,
    EmbeddingProvider,
    MemoryProviderError,
    RerankerProvider,
)
from evolving_companion.memory_retrieval import MemoryRetriever
from evolving_companion.memory_recall import MemoryRecallService
from evolving_companion.memory_reranker import MemoryReranker
from evolving_companion.memory_retrieval_benchmark import (
    calculate_metrics,
    load_cases,
    FIXTURE_PATH,
)
from evolving_companion.storage import SQLiteStore

EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-8B"
RERANKER_MODEL = "Qwen/Qwen3-Reranker-8B"
BASE_URL = "https://api.siliconflow.cn/v1"
# Explicit expected configuration, checked against every response; no dimension inference.
DEFAULT_DIMENSION = 4096
KEY_ERROR = "SiliconFlow API key not configured (缺少 SILICONFLOW_API_KEY). Please add SILICONFLOW_API_KEY to .env.local."


def require_api_key() -> str:
    key = os.environ.get("SILICONFLOW_API_KEY", "")
    if not key.strip():
        raise ValueError(KEY_ERROR)
    return key


def _token_count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


@dataclass
class RequestStats:
    records: list[dict[str, Any]] = field(default_factory=list)

    def mark_malformed(self, start: int) -> None:
        if len(self.records) > start and self.records[-1]["success"]:
            self.records[-1].update(success=False, failure_kind="malformed_response")

    def summary(self, price_per_million: float | None = None) -> dict[str, Any]:
        latencies = [item["seconds"] for item in self.records]
        warm = latencies[1:]
        input_counts = [
            item["input_tokens"]
            for item in self.records
            if item.get("input_tokens") is not None
        ]
        totals = [
            item["total_tokens"]
            for item in self.records
            if item.get("total_tokens") is not None
        ]
        return {
            "total_requests": len(self.records),
            "successes": sum(item["success"] for item in self.records),
            "failures": sum(not item["success"] for item in self.records),
            "average_seconds": float(np.mean(latencies)) if latencies else None,
            "p50_seconds": float(np.percentile(latencies, 50)) if latencies else None,
            "p95_seconds": float(np.percentile(latencies, 95)) if latencies else None,
            "first_request_seconds": latencies[0] if latencies else None,
            "warm_requests": len(warm),
            "warm_average_seconds": float(np.mean(warm)) if warm else None,
            "warm_p50_seconds": float(np.percentile(warm, 50)) if warm else None,
            "warm_p95_seconds": float(np.percentile(warm, 95)) if warm else None,
            "input_tokens": sum(input_counts) if input_counts else None,
            "total_tokens": sum(totals) if totals else None,
            "requests_with_input_usage": len(input_counts),
            "requests_with_total_usage": len(totals),
            "price_per_million_input_tokens": price_per_million,
            "estimated_cost": sum(input_counts) * price_per_million / 1_000_000
            if price_per_million is not None
            and len(input_counts) == len(self.records)
            and self.records
            else None,
        }


class RecordingTransport(httpx.BaseTransport):
    """Collect only safe response facts, never request headers/body or error text."""

    def __init__(
        self, stats: RequestStats, delegate: httpx.BaseTransport | None = None
    ):
        self.stats = stats
        self.delegate = (
            delegate if delegate is not None else httpx.HTTPTransport(retries=0)
        )

    def close(self) -> None:
        self.delegate.close()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        started = perf_counter()
        record: dict[str, Any] = {
            "success": False,
            "http_status": None,
            "failure_kind": None,
            "input_tokens": None,
            "total_tokens": None,
        }
        try:
            response = self.delegate.handle_request(request)
            response.read()
            record["http_status"] = response.status_code
            record["success"] = response.is_success
            if not response.is_success:
                record["failure_kind"] = (
                    "rate_limit"
                    if response.status_code == 429
                    else "server_error"
                    if response.status_code >= 500
                    else "http_error"
                )
            else:
                try:
                    body = response.json()
                    if not isinstance(body, dict):
                        raise ValueError("invalid response object")
                    usage = body.get("usage", {})
                    tokens = body.get("tokens", {})
                    usage = usage if isinstance(usage, dict) else {}
                    tokens = tokens if isinstance(tokens, dict) else {}
                    record["input_tokens"] = _token_count(
                        usage.get(
                            "prompt_tokens",
                            usage.get("input_tokens", tokens.get("input_tokens")),
                        )
                    )
                    record["total_tokens"] = _token_count(usage.get("total_tokens"))
                    if (
                        record["total_tokens"] is None
                        and record["input_tokens"] is not None
                    ):
                        output = _token_count(tokens.get("output_tokens"))
                        if output is not None:
                            record["total_tokens"] = record["input_tokens"] + output
                except (ValueError, AttributeError, TypeError):
                    record.update(success=False, failure_kind="malformed_response")
            return response
        except httpx.TimeoutException:
            record["failure_kind"] = "timeout"
            raise
        except httpx.HTTPError:
            record["failure_kind"] = "transport_error"
            raise
        finally:
            record["seconds"] = perf_counter() - started
            self.stats.records.append(record)


def create_siliconflow_providers(
    *,
    dimension: int = DEFAULT_DIMENSION,
    timeout: float = 30,
    delegate: httpx.BaseTransport | None = None,
) -> tuple[APIEmbeddingProvider, APIRerankerProvider, RequestStats, RequestStats]:
    key = require_api_key()
    embedding_stats, reranker_stats = RequestStats(), RequestStats()
    embedding = APIEmbeddingProvider(
        provider_id="siliconflow-cn",
        model_id=EMBEDDING_MODEL,
        dimension=dimension,
        endpoint=f"{BASE_URL}/embeddings",
        api_key=key,
        timeout=timeout,
        transport=RecordingTransport(embedding_stats, delegate),
    )
    reranker = APIRerankerProvider(
        model_id=RERANKER_MODEL,
        endpoint=f"{BASE_URL}/rerank",
        api_key=key,
        timeout=timeout,
        transport=RecordingTransport(reranker_stats, delegate),
    )
    return embedding, reranker, embedding_stats, reranker_stats


class MeasuredEmbedding:
    def __init__(
        self, provider: EmbeddingProvider, stats: RequestStats, *, local: bool = False
    ):
        self.provider = provider
        self.provider_id, self.model_id, self.dimension = (
            provider.provider_id,
            provider.model_id,
            provider.dimension,
        )
        self.stats, self.local = stats, local

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        started, count = perf_counter(), len(self.stats.records)
        success = False
        try:
            vectors = self.provider.embed_texts(texts)
            success = True
            return vectors
        except MemoryProviderError:
            self.stats.mark_malformed(count)
            raise
        finally:
            if self.local:
                self.stats.records.append(
                    {"success": success, "seconds": perf_counter() - started}
                )


class MeasuredReranker:
    def __init__(
        self, provider: RerankerProvider, stats: RequestStats, *, local: bool = False
    ):
        self.provider, self.stats, self.local = provider, stats, local

    def score(self, query: str, texts: Sequence[str]) -> Sequence[float]:
        started, count = perf_counter(), len(self.stats.records)
        success = False
        try:
            scores = self.provider.score(query, texts)
            success = True
            return scores
        except MemoryProviderError:
            self.stats.mark_malformed(count)
            raise
        finally:
            if self.local:
                self.stats.records.append(
                    {"success": success, "seconds": perf_counter() - started}
                )


def run_profile(
    name: str,
    embedding: EmbeddingProvider,
    reranker: RerankerProvider,
    embedding_stats: RequestStats,
    reranker_stats: RequestStats,
    *,
    fixture_path: Path = FIXTURE_PATH,
    local: bool = False,
    latency_profile: bool = False,
) -> dict[str, Any]:
    fixture = load_cases(fixture_path)
    result: dict[str, Any] = {
        "profile": name,
        "provider": embedding.provider_id,
        "embedding_model": embedding.model_id,
        "dimension": embedding.dimension,
        "dimension_verified": False,
        "reranker_model": getattr(reranker, "model_id", "unspecified"),
        "complete": False,
        "failure": None,
        "queries": [],
        "semantic_metrics": None,
        "reranked_metrics": None,
        "latency_scope": "local encode/score calls including cold load"
        if local
        else "HTTP requests including handshake/body read, excluding local parsing",
    }
    with TemporaryDirectory(prefix="si001-siliconflow-benchmark-") as directory:
        store = SQLiteStore(Path(directory) / "benchmark.db")
        ids = {
            store.create_memory(
                "semantic", memory["content"], "explicit", "medium"
            ).id: memory["id"]
            for memory in fixture["memories"]
        }
        retriever = MemoryRetriever(
            store, MeasuredEmbedding(embedding, embedding_stats, local=local)
        )
        ranker = MemoryReranker(
            provider=MeasuredReranker(reranker, reranker_stats, local=local)
        )
        semantic_rankings, reranked_rankings = {}, {}
        try:
            for query in fixture["queries"]:
                candidates = retriever.retrieve(query["text"], top_n=10)
                result["dimension_verified"] = True
                ranked = ranker.rerank(query["text"], candidates, top_n=10)
                semantic = [
                    {**asdict(item), "memory_id": ids[item.memory_id]}
                    for item in candidates
                ]
                reranked = [
                    {**asdict(item), "memory_id": ids[item.memory_id]}
                    for item in ranked
                ]
                semantic_rankings[query["id"]] = [
                    item["memory_id"] for item in semantic
                ]
                reranked_rankings[query["id"]] = [
                    item["memory_id"] for item in reranked
                ]
                result["queries"].append(
                    {
                        **query,
                        "semantic_top10": semantic,
                        "reranked_top10": reranked,
                        "injection_top3": reranked[:3],
                    }
                )
                print(
                    f"{name} | {query['id']} | {query['text']} | expected={query['expected_memory_ids']}"
                )
                for rank, item in enumerate(reranked[:5], 1):
                    print(
                        f"  {rank}. {item['memory_id']} | similarity={item['semantic_similarity']:.6f} | score={item['raw_score']:.6f} | {item['content']}"
                    )
                print(
                    "  expected ranks:",
                    {
                        memory_id: reranked_rankings[query["id"]].index(memory_id) + 1
                        if memory_id in reranked_rankings[query["id"]]
                        else "outside semantic Top-10"
                        for memory_id in query["expected_memory_ids"]
                    },
                )
            result["semantic_metrics"] = calculate_metrics(
                fixture["queries"], semantic_rankings
            )
            result["reranked_metrics"] = calculate_metrics(
                fixture["queries"], reranked_rankings
            )
            if latency_profile:
                result["recall_latency"] = measure_recall_latency(
                    retriever, ranker, embedding_stats, reranker_stats
                )
            result["complete"] = True
        except (MemoryProviderError, ImportError, OSError, ValueError) as error:
            # No unsafe traceback or vendor body in reports; fail-fast, no retry.
            result["failure"] = (
                str(error)
                if isinstance(error, MemoryProviderError)
                else type(error).__name__
            )
            print(f"{name} failed; no retry: {result['failure']}")
    return result


def measure_recall_latency(
    retriever: MemoryRetriever,
    ranker: MemoryReranker,
    embedding_stats: RequestStats,
    reranker_stats: RequestStats,
) -> dict[str, Any]:
    """Warm-index production Need Gate path; synthetic queries, no LLM reply."""
    recall = MemoryRecallService(retriever, ranker)
    rows = []
    for query in ["你还记得我的计划吗？"] * 5 + ["你好"]:
        counts = (len(embedding_stats.records), len(reranker_stats.records))
        started = perf_counter()
        result = recall.recall(query, [])
        seconds = perf_counter() - started
        delta = (
            len(embedding_stats.records) - counts[0],
            len(reranker_stats.records) - counts[1],
        )
        expected = (1, 1) if result.memory_needed else (0, 0)
        if delta != expected:
            raise ValueError("unexpected provider calls in warm-index recall")
        rows.append(
            {
                "query": query,
                "memory_needed": result.memory_needed,
                "seconds": seconds,
                "embedding_calls": delta[0],
                "reranker_calls": delta[1],
                "top3_count": len(result.recalled_memories),
            }
        )
    timings = [row["seconds"] for row in rows if row["memory_needed"]]
    return {
        "scope": "warm index: Need Gate + query embedding + SQLite/cosine + rerank + Top-3; no conversation LLM",
        "samples": rows,
        "need_true_average_seconds": float(np.mean(timings)),
        "need_true_p50_seconds": float(np.percentile(timings, 50)),
        "need_true_p95_seconds": float(np.percentile(timings, 95)),
    }


def benchmark_setup(path: Path = FIXTURE_PATH) -> dict[str, Any]:
    fixture = load_cases(path)
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fixture": path.name,
        "fixture_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "memory_count": len(fixture["memories"]),
        "query_count": len(fixture["queries"]),
        "expected_query_count": sum(
            bool(query["expected_memory_ids"]) for query in fixture["queries"]
        ),
        "no_related_query_count": sum(
            not query["expected_memory_ids"] for query in fixture["queries"]
        ),
        "query_mode": "raw; no added instruction",
        "semantic_top_n": 10,
        "injection_top_k": 3,
        "retry_policy": "fail-fast; zero retries",
    }


def render_report(result: dict[str, Any]) -> str:
    sections = [
        "# A6.1 SiliconFlow Qwen3 Retrieval Benchmark",
        "",
        "## 1. Benchmark setup",
        "",
        str(result["setup"]),
        "",
        "## 2. Models",
        "",
    ]
    for profile in result["profiles"]:
        sections += [
            f"{profile['profile']}: {profile['embedding_model']} → {profile['reranker_model']}; configured dimension={profile['dimension']}, response_verified={profile['dimension_verified']}",
            "",
        ]
    sections += [
        "## 3. Dataset size",
        "",
        f"{result['setup']['memory_count']} memories / {result['setup']['query_count']} queries; no-related queries excluded from Recall/MRR.",
        "",
        "## 4. Metrics",
        "",
        "MRR is bounded by semantic Top-10. Recall is macro average of recovered expected IDs, not hit rate.",
        "",
    ]
    for profile in result["profiles"]:
        sections += [
            f"### {profile['profile']}",
            "",
            f"{profile['embedding_model']} → {profile['reranker_model']}; dimension={profile['dimension']}; complete={profile['complete']}",
            f"Semantic: {profile['semantic_metrics']}",
            f"Reranked: {profile['reranked_metrics']}",
            "",
        ]
    sections += [
        "## 5. Latency",
        "",
        "p50/p95 use linear percentiles; zero retries. First request is process/client-cold, not proven server model cold. Warm excludes first request; embedding first request indexes 32 memories, later payloads differ. Local first call includes lazy model load. Pools are reused, but TCP reuse is not guaranteed by the server.",
        "",
    ]
    for profile in result["profiles"]:
        sections += [
            f"{profile['profile']} ({profile['latency_scope']}):",
            "",
            f"Embedding: {profile['embedding_summary']}",
            f"Reranker: {profile['reranker_summary']}",
            f"End-to-end recall: {profile.get('recall_latency', 'not requested')}",
            "",
        ]
    sections += [
        "## 6. Token usage / Cost",
        "",
        f"Currency: {result['currency']}. Missing usage is null, never estimated as zero. Cost requires an explicit price and input usage for every request.",
        "",
        "## 7. Failure count",
        "",
    ]
    for profile in result["profiles"]:
        sections += [
            f"{profile['profile']}: {profile['embedding_summary']['failures'] + profile['reranker_summary']['failures']} failed requests; profile error={profile['failure']}",
            "",
        ]
    sections += [
        "## 8. Observations",
        "",
        "No-related queries still return candidates (no threshold). Full per-query rankings and expected positions are in the paired JSON. This small synthetic fixture and a single timing run do not prove generalization, production SLA or universal relevance.",
        "",
    ]
    for profile in result["profiles"]:
        for query in profile["queries"]:
            if not query["expected_memory_ids"]:
                top = query["semantic_top10"][0]
                sections += [
                    f"{profile['profile']} / {query['id']}: semantic Top-1={top['memory_id']}, similarity={top['similarity']:.6f}; reranked Top-1={query['reranked_top10'][0]['memory_id']}",
                    "",
                ]
    sections += [
        "## 9. Decision status",
        "",
        result["decision"],
        "",
        "Decision is a manual review label, not an automatic winner. Production defaults remain unchanged (local/local).",
        "",
    ]
    return "\n".join(sections)
