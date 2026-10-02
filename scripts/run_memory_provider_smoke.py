"""Offline fake HTTP providers exercise production retrieval with a temporary DB."""

import json
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from evolving_companion.character_projection import ProjectedCharacterContext
from evolving_companion.conversation import Conversation
from evolving_companion.prompting import Message
from evolving_companion.memory_providers import (
    APIEmbeddingProvider,
    APIRerankerProvider,
)
from evolving_companion.memory_recall import MemoryRecallService
from evolving_companion.memory_reranker import MemoryReranker
from evolving_companion.memory_retrieval import MemoryRetriever
from evolving_companion.storage import SQLiteStore


def main() -> None:
    calls: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        if request.url.path == "/embeddings":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": index, "embedding": [1.0, index + 1.0]}
                        for index, _ in enumerate(payload["input"])
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": index, "relevance_score": float(index)}
                    for index, _ in enumerate(payload["documents"])
                ]
            },
        )

    def embedding(
        model: str, provider_id: str = "offline-fake-api"
    ) -> APIEmbeddingProvider:
        provider = APIEmbeddingProvider(
            provider_id=provider_id,
            model_id=model,
            dimension=2,
            endpoint="https://offline.invalid/embeddings",
            api_key="fake-not-secret",
            transport=httpx.MockTransport(respond),
        )
        resources.callback(provider.close)
        return provider

    with (
        TemporaryDirectory(prefix="si001-memory-provider-") as directory,
        ExitStack() as resources,
    ):
        store = SQLiteStore(Path(directory) / "smoke.db")
        for index in range(12):
            store.create_memory(
                "semantic", f"合成测试记忆 {index}", "explicit", "medium"
            )
        before = store.list_active_memories()
        provider = embedding("fake-v1")
        retriever = MemoryRetriever(store, provider)
        reranker = APIRerankerProvider(
            endpoint="https://offline.invalid/rerank",
            api_key="fake-not-secret",
            model_id="fake-rerank",
            transport=httpx.MockTransport(respond),
        )
        resources.callback(reranker.close)
        result = MemoryRecallService(
            retriever, MemoryReranker(provider=reranker)
        ).recall("你还记得我的计划吗？", [])
        assert len(result.candidates) == 10 and len(result.recalled_memories) == 3
        print("Offline fake API: semantic Top-10 -> rerank -> Top-3 verified")
        for item in result.recalled_memories:
            print(
                f"{item.content} | cosine={item.semantic_similarity:.4f} | score={item.raw_score:.4f}"
            )
        client = provider._client
        reranker_client = reranker._client
        retriever.retrieve("test")
        reranker.score("test", ["合成测试"])
        assert provider._client is client and reranker._client is reranker_client
        count = len(calls)
        assert (
            not MemoryRecallService(retriever, MemoryReranker(provider=reranker))
            .recall("你好", [])
            .memory_needed
        )
        assert len(calls) == count
        assert sum("input" in call and len(call["input"]) == 12 for call in calls) == 1
        switched = MemoryRetriever(store, embedding("fake-v2"))
        switched.retrieve("test")
        assert (
            len(
                store.get_memory_embeddings(
                    [item.id for item in before], switched.index_key
                )
            )
            == 12
        )
        assert sum("input" in call and len(call["input"]) == 12 for call in calls) == 2
        assert store.list_active_memories() == before
        assert switched.rebuild_memory_embeddings() == 12
        changed_provider = MemoryRetriever(
            store, embedding("fake-v2", "other-fake-api")
        )
        changed_provider.retrieve("test")
        assert (
            len(
                store.get_memory_embeddings(
                    [item.id for item in before], changed_provider.index_key
                )
            )
            == 12
        )
        assert store.list_active_memories() == before
        print(
            "Persistent clients, Need=false no calls, cache reuse, model/provider-switch and explicit rebuild verified; authoritative memories unchanged"
        )
        for failure in ("embedding", "reranker"):

            def failing_response(request: httpx.Request) -> httpx.Response:
                if failure == "embedding" and request.url.path == "/embeddings":
                    raise httpx.ReadTimeout("synthetic timeout", request=request)
                if failure == "reranker" and request.url.path == "/rerank":
                    return httpx.Response(503)
                return respond(request)

            failing_embedding = APIEmbeddingProvider(
                provider_id="offline-fake-api",
                model_id="fake-v1",
                dimension=2,
                endpoint="https://offline.invalid/embeddings",
                api_key="fake-not-secret",
                transport=httpx.MockTransport(failing_response),
            )
            failing_reranker = APIRerankerProvider(
                model_id="fake-rerank",
                endpoint="https://offline.invalid/rerank",
                api_key="fake-not-secret",
                transport=httpx.MockTransport(failing_response),
            )
            resources.callback(failing_embedding.close)
            resources.callback(failing_reranker.close)

            class Client:
                def complete(self, messages: list[Message]) -> str:
                    assert "【可参考的长期记忆候选】" not in messages[0]["content"]
                    assert "memory_recall_failed" not in messages[0]["content"]
                    return "正常回复"

            conversation = Conversation(
                Client(),
                ProjectedCharacterContext("玲"),
                store,
                memory_recall_service=MemoryRecallService(
                    MemoryRetriever(store, failing_embedding),
                    MemoryReranker(provider=failing_reranker),
                ),
            )
            assert conversation.send("你还记得我的计划吗？") == "正常回复"
            assert conversation.last_memory_recall_error == "MemoryProviderError"
            assert len(conversation.history) == 2
            assert store.list_active_memories() == before
            print(
                f"{failure} failure -> no-memory prompt -> normal Character reply verified"
            )
        print(f"Temporary DB: {store.path} (removed on exit)")
    assert client is not None and client.is_closed
    assert reranker_client is not None and reranker_client.is_closed
    print("Smoke passed; provider clients closed; temporary database removed")


if __name__ == "__main__":
    main()
