import builtins
import json
from pathlib import Path
from runpy import run_path
import sys
from typing import Any

import httpx
import numpy as np
import pytest

from evolving_companion.local_env import load_local_env
from evolving_companion.memory_providers import MemoryProviderError
from evolving_companion.memory_retrieval_benchmark import calculate_metrics
from evolving_companion.siliconflow_benchmark import (
    EMBEDDING_MODEL,
    RERANKER_MODEL,
    MeasuredEmbedding,
    MeasuredReranker,
    RequestStats,
    create_siliconflow_providers,
    run_profile,
)


def response(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    if request.url.path == "/v1/embeddings":
        assert payload["model"] == EMBEDDING_MODEL
        assert payload["encoding_format"] == "float"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": [3, 4]}
                    for index, _ in enumerate(payload["input"])
                ],
                "usage": {"prompt_tokens": 10, "total_tokens": 10},
            },
        )
    assert payload["model"] == RERANKER_MODEL
    assert payload["query"]
    assert payload["top_n"] == len(payload["documents"])
    return httpx.Response(
        200,
        json={
            "results": [
                {"index": index, "relevance_score": -index}
                for index, _ in enumerate(payload["documents"])
            ],
            "tokens": {"input_tokens": 20, "output_tokens": 0},
        },
    )


def providers(monkeypatch: pytest.MonkeyPatch, handler=response):
    monkeypatch.setenv("SILICONFLOW_API_KEY", "fake-secret-not-real")
    return create_siliconflow_providers(
        dimension=2, delegate=httpx.MockTransport(handler)
    )


def test_request_parsing_metadata_usage_and_safe_output(
    monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    caplog.set_level("DEBUG")
    embedding, reranker, embedding_stats, reranker_stats = providers(monkeypatch)
    assert embedding.provider_id == "siliconflow-cn"
    assert embedding.dimension == 2
    assert np.allclose(embedding.embed_texts(["synthetic"]), [[0.6, 0.8]])
    assert reranker.score("query", ["a", "b", "c"]) == [0, -1, -2]
    assert embedding_stats.summary()["input_tokens"] == 10
    assert reranker_stats.summary()["total_tokens"] == 20
    assert "fake-secret-not-real" not in caplog.text + json.dumps(
        embedding_stats.records
    ) + json.dumps(reranker_stats.records)


def test_missing_key_and_isolated_env_priority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    load_local_env(tmp_path / "does-not-exist")
    with pytest.raises(ValueError, match="缺少 SILICONFLOW_API_KEY"):
        create_siliconflow_providers()
    env_file = tmp_path / ".env.local"
    env_file.write_text("SILICONFLOW_API_KEY=fake-file-key\n", encoding="utf-8")
    load_local_env(env_file)
    embedding, *_ = create_siliconflow_providers()
    assert embedding.model_id == EMBEDDING_MODEL
    monkeypatch.setenv("SILICONFLOW_API_KEY", "fake-process-key")
    load_local_env(env_file)

    def check(request):
        assert request.headers["Authorization"] == "Bearer fake-process-key"
        return response(request)

    embedding, *_ = create_siliconflow_providers(
        dimension=2, delegate=httpx.MockTransport(check)
    )
    embedding.embed_texts(["synthetic"])


@pytest.mark.parametrize("kind", ["embedding", "reranker"])
def test_malformed_response_is_recorded(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    embedding, reranker, embedding_stats, reranker_stats = providers(
        monkeypatch,
        lambda request: httpx.Response(200, json={"data": [], "results": []}),
    )
    with pytest.raises(MemoryProviderError):
        if kind == "embedding":
            MeasuredEmbedding(embedding, embedding_stats).embed_texts(["synthetic"])
        else:
            MeasuredReranker(reranker, reranker_stats).score("query", ["synthetic"])
    stats = embedding_stats if kind == "embedding" else reranker_stats
    assert stats.records[0]["failure_kind"] == "malformed_response"
    assert stats.summary()["failures"] == 1


@pytest.mark.parametrize(
    "status, expected",
    [(401, "http_error"), (429, "rate_limit"), (503, "server_error")],
)
def test_http_failures_safe_and_no_retry(
    monkeypatch: pytest.MonkeyPatch, status: int, expected: str
) -> None:
    embedding, _, stats, _ = providers(
        monkeypatch, lambda request: httpx.Response(status, text="fake-secret-not-real")
    )
    with pytest.raises(MemoryProviderError) as error:
        MeasuredEmbedding(embedding, stats).embed_texts(["synthetic"])
    assert "fake-secret-not-real" not in str(error.value) + json.dumps(stats.records)
    assert len(stats.records) == 1
    assert stats.records[0]["failure_kind"] == expected


def test_timeout_is_recorded_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(request):
        raise httpx.ReadTimeout("fake-secret-not-real", request=request)

    embedding, _, stats, _ = providers(monkeypatch, timeout)
    with pytest.raises(MemoryProviderError, match="timed out"):
        embedding.embed_texts(["synthetic"])
    assert len(stats.records) == 1
    assert stats.records[0]["failure_kind"] == "timeout"


def test_latency_cost_and_missing_usage_not_fabricated() -> None:
    stats = RequestStats(
        [
            {
                "success": True,
                "seconds": seconds,
                "input_tokens": 100,
                "total_tokens": 100,
            }
            for seconds in (1, 2, 3)
        ]
    )
    summary = stats.summary(2)
    assert summary["average_seconds"] == 2
    assert summary["p50_seconds"] == 2
    assert summary["p95_seconds"] == pytest.approx(2.9)
    assert summary["estimated_cost"] == pytest.approx(0.0006)
    stats.records.append({"success": False, "seconds": 4})
    assert stats.summary(2)["estimated_cost"] is None
    assert RequestStats().summary()["input_tokens"] is None


def test_metrics_and_no_related_exclusion() -> None:
    queries = [
        {"id": "one", "expected_memory_ids": ["a", "b"]},
        {"id": "none", "expected_memory_ids": []},
    ]
    assert calculate_metrics(queries, {"one": ["x", "a", "b"], "none": ["x"]}) == {
        "recall@1": 0,
        "recall@3": 1,
        "recall@5": 1,
        "mrr": 0.5,
    }


def test_profile_failure_no_partial_metrics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fixture = tmp_path / "cases.json"
    fixture.write_text(
        json.dumps(
            {
                "memories": [{"id": "m", "content": "synthetic"}],
                "queries": [{"id": "q", "text": "query", "expected_memory_ids": ["m"]}],
            }
        ),
        encoding="utf-8",
    )
    embedding, reranker, embedding_stats, reranker_stats = providers(
        monkeypatch, lambda request: httpx.Response(503)
    )
    result = run_profile(
        "fake",
        embedding,
        reranker,
        embedding_stats,
        reranker_stats,
        fixture_path=fixture,
    )
    assert not result["complete"]
    assert not result["dimension_verified"]
    assert result["semantic_metrics"] is None
    assert len(embedding_stats.records) == 1
    assert reranker_stats.records == []


def test_api_only_script_with_mock_transport_never_loads_bge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    namespace = run_path(
        str(
            Path(__file__).parents[1] / "scripts/run_siliconflow_retrieval_benchmark.py"
        )
    )
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert not name.startswith(("sentence_transformers", "torch", "transformers"))
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "fake-secret-not-real")
    main = namespace["main"]
    monkeypatch.setitem(main.__globals__, "load_local_env", lambda: None)
    monkeypatch.setitem(
        main.__globals__,
        "create_siliconflow_providers",
        lambda **kwargs: create_siliconflow_providers(
            dimension=2, delegate=httpx.MockTransport(response)
        ),
    )
    monkeypatch.setattr(
        sys, "argv", ["benchmark", "--output-dir", str(tmp_path / "results")]
    )
    assert main() == 0
    result: dict[str, Any] = json.loads(
        next((tmp_path / "results").glob("*.json")).read_text(encoding="utf-8")
    )
    assert len(result["profiles"]) == 1
    assert result["profiles"][0]["complete"]
    assert result["profiles"][0]["dimension_verified"]
    assert len(result["profiles"][0]["queries"][0]["injection_top3"]) == 3
    assert "fake-secret-not-real" not in json.dumps(result)
