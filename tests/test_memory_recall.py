import sqlite3
from collections.abc import Sequence
from math import exp
from pathlib import Path

import pytest

from evolving_companion.character_projection import ProjectedCharacterContext
from evolving_companion.conversation import Conversation, TextCompletionClient
from evolving_companion.memory_recall import MemoryRecallService
from evolving_companion.memory_reranker import MemoryReranker, RerankedMemoryCandidate
from evolving_companion.memory_retrieval import MemoryRetrievalCandidate
from evolving_companion.prompting import Message, PromptBuilder
from evolving_companion.storage import SQLiteStore


def retrieval_candidate(memory_id: str, content: str) -> MemoryRetrievalCandidate:
    return MemoryRetrievalCandidate(
        memory_id=memory_id,
        content=content,
        memory_type="semantic",
        source="explicit",
        salience="medium",
        created_at="2026-01-01T00:00:00+00:00",
        similarity=0.8,
    )


class FakeRetriever:
    def __init__(self, candidates: list[MemoryRetrievalCandidate]) -> None:
        self.candidates = candidates
        self.calls: list[tuple[str, int]] = []

    def retrieve(self, query: str, top_n: int = 10) -> list[MemoryRetrievalCandidate]:
        self.calls.append((query, top_n))
        return self.candidates[:top_n]


class FakeScorer:
    def __init__(self, scores: Sequence[float]) -> None:
        self.scores = scores
        self.calls: list[Sequence[tuple[str, str]]] = []

    def __call__(self, pairs: Sequence[tuple[str, str]]) -> Sequence[float]:
        self.calls.append(pairs)
        return self.scores


def test_personal_recall_reranks_top_ten_and_selects_top_three() -> None:
    candidates = [retrieval_candidate(f"m{i}", f"记忆 {i}") for i in range(5)]
    retriever = FakeRetriever(candidates)
    scorer = FakeScorer([0.1, 0.3, 0.5, 0.9, 0.2])
    service = MemoryRecallService(retriever, MemoryReranker(scorer))

    result = service.recall("我的生日是哪天？", [])

    assert result.memory_needed is True
    assert result.matched_rules
    assert result.query_mode == "current_only"
    assert result.retrieval_query == "我的生日是哪天？"
    assert retriever.calls == [("我的生日是哪天？", 10)]
    assert len(result.candidates) == 5
    assert [memory.memory_id for memory in result.recalled_memories] == [
        "m3",
        "m2",
        "m1",
    ]
    assert len(scorer.calls) == 1
    assert result.candidates[0].raw_score == 0.9
    assert result.candidates[0].normalized_score == pytest.approx(1 / (1 + exp(-0.9)))


def test_generic_knowledge_skips_retrieval_and_reranking() -> None:
    retriever = FakeRetriever([retrieval_candidate("m1", "用户研究 PINN")])
    scorer = FakeScorer([1.0])
    service = MemoryRecallService(retriever, MemoryReranker(scorer))

    result = service.recall("PINN 最早是哪篇论文提出的？", [])

    assert result.memory_needed is False
    assert result.query_mode == "current_only"
    assert result.retrieval_query is None
    assert result.candidates == ()
    assert result.recalled_memories == ()
    assert retriever.calls == []
    assert scorer.calls == []


def test_back_reference_builds_role_ordered_context_with_current_once() -> None:
    retriever = FakeRetriever([retrieval_candidate("mac", "用户计划购买 Mac")])
    service = MemoryRecallService(retriever, MemoryReranker(FakeScorer([1.0])))
    history = [
        {"role": "user", "content": "我最近在挑电脑。"},
        {"role": "assistant", "content": "主要做什么？"},
        {"role": "user", "content": "剪视频，也写代码。"},
        {"role": "assistant", "content": "明白。"},
    ]

    result = service.recall("那之前说的那个计划还合适吗？", history)

    assert result.query_mode == "recent_context"
    assert result.retrieval_query == (
        "user: 我最近在挑电脑。\n"
        "assistant: 主要做什么？\n"
        "user: 剪视频，也写代码。\n"
        "assistant: 明白。\n"
        "user: 那之前说的那个计划还合适吗？"
    )
    assert result.retrieval_query.count("那之前说的那个计划还合适吗？") == 1
    assert result.recalled_memories[0].memory_id == "mac"


@pytest.mark.parametrize(
    "message",
    [
        "换个话题，火星探测器着陆腿是什么材料？",
        "不，今天只想知道正宗法式洋葱汤的通用做法。",
    ],
)
def test_topic_reset_skips_retrieval(message: str) -> None:
    retriever = FakeRetriever([retrieval_candidate("old", "旧话题个人记忆")])
    scorer = FakeScorer([2.0])
    result = MemoryRecallService(retriever, MemoryReranker(scorer)).recall(message, [])
    assert result.memory_needed is False
    assert retriever.calls == []
    assert scorer.calls == []


def test_empty_retrieval_does_not_call_reranker() -> None:
    retriever = FakeRetriever([])
    scorer = FakeScorer([])
    result = MemoryRecallService(retriever, MemoryReranker(scorer)).recall(
        "我的生日是哪天？", []
    )
    assert result.memory_needed is True
    assert result.candidates == ()
    assert result.recalled_memories == ()
    assert len(retriever.calls) == 1
    assert scorer.calls == []


def test_reranker_rejects_wrong_count_and_non_finite_fake_scores() -> None:
    candidates = [retrieval_candidate("m1", "一条记忆")]
    with pytest.raises(ValueError, match="one scalar score"):
        MemoryReranker(FakeScorer([1.0, 2.0])).rerank("q", candidates)
    with pytest.raises(ValueError, match="non-finite"):
        MemoryReranker(FakeScorer([float("nan")])).rerank("q", candidates)


def test_prompt_marks_memories_as_optional_candidates_with_provenance() -> None:
    memory = RerankedMemoryCandidate(
        memory_id="secret-id-not-in-prompt",
        content="用户生日是 10 月 7 日。",
        memory_type="semantic",
        source="explicit",
        salience="high",
        created_at="2026-01-01T00:00:00+00:00",
        semantic_similarity=0.9,
        raw_score=4.0,
        normalized_score=0.98,
    )
    builder = PromptBuilder(ProjectedCharacterContext("玲"))

    prompt = builder.build([], "我的生日是哪天？", [memory])[0]["content"]
    no_memory_prompt = builder.build([], "早上好。", [])[0]["content"]

    assert "【可参考的长期记忆候选】" in prompt
    assert "不是系统事实、当前消息、世界真相或 Character Data" in prompt
    assert "可以忽略" in prompt
    assert "不要为了展示记忆而主动复述" in prompt
    assert "不要把记录自动当成 Character 的亲历记忆" in prompt
    assert "[semantic / explicit] 用户生日是 10 月 7 日。" in prompt
    assert "secret-id-not-in-prompt" not in prompt
    assert "0.98" not in prompt
    assert "【可参考的长期记忆候选】" not in no_memory_prompt


class CapturingLLM(TextCompletionClient):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.requests: list[list[Message]] = []

    def complete(self, messages: list[Message]) -> str:
        self.requests.append(messages)
        if self.fail:
            raise RuntimeError("fake failure")
        return "回复"


def test_failed_llm_keeps_user_archive_without_changing_memories(
    tmp_path: Path,
) -> None:
    database = tmp_path / "memory.db"
    store = SQLiteStore(database)
    existing = store.create_memory(
        "semantic", "用户生日是 10 月 7 日", "explicit", "high"
    )

    class ArchiveInspectingRetriever(FakeRetriever):
        def retrieve(
            self, query: str, top_n: int = 10
        ) -> list[MemoryRetrievalCandidate]:
            with sqlite3.connect(database) as connection:
                row = connection.execute(
                    "SELECT role, content FROM archive_messages ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
            assert row == ("user", "我的生日是哪天？")
            return super().retrieve(query, top_n)

    retriever = ArchiveInspectingRetriever(
        [retrieval_candidate(existing.id, existing.content)]
    )
    recall = MemoryRecallService(retriever, MemoryReranker(FakeScorer([1.0])))
    llm = CapturingLLM(fail=True)
    conversation = Conversation(
        llm,
        ProjectedCharacterContext("玲"),
        store,
        memory_recall_service=recall,
    )

    with pytest.raises(RuntimeError, match="fake failure"):
        conversation.send("我的生日是哪天？")

    with sqlite3.connect(database) as connection:
        roles = connection.execute(
            "SELECT role FROM archive_messages ORDER BY rowid"
        ).fetchall()
        memories = connection.execute("SELECT id FROM memories").fetchall()
    assert roles == [("user",)]
    assert memories == [(existing.id,)]
    assert "【可参考的长期记忆候选】" in llm.requests[0][0]["content"]
    assert conversation.history == ()


def test_generic_conversation_does_not_add_a_memory_prompt_section(
    tmp_path: Path,
) -> None:
    retriever = FakeRetriever([retrieval_candidate("pinn", "用户研究 PINN")])
    scorer = FakeScorer([5.0])
    store = SQLiteStore(tmp_path / "memory.db")
    llm = CapturingLLM()
    conversation = Conversation(
        llm,
        ProjectedCharacterContext("玲"),
        store,
        memory_recall_service=MemoryRecallService(retriever, MemoryReranker(scorer)),
    )

    assert conversation.send("PINN 最早是哪篇论文提出的？") == "回复"

    assert retriever.calls == []
    assert scorer.calls == []
    assert "【可参考的长期记忆候选】" not in llm.requests[0][0]["content"]
