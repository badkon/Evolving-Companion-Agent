from collections.abc import Mapping, Sequence
from runpy import run_path
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from evolving_companion.character_projection import ProjectedCharacterContext
from evolving_companion.conversation import Conversation, TextCompletionClient
from evolving_companion.embeddings import EMBEDDING_DIMENSIONS
from evolving_companion.memory_consolidation import (
    ConsolidationDecision,
    ConsolidationJudgment,
    MemoryConsolidationJudge,
    MemoryConsolidationService,
)
from evolving_companion.memory_extraction import (
    ArchiveChunkMessage,
    MemoryCandidate,
    MemoryExtractionResult,
    TextCompletionClient as ExtractionTextCompletionClient,
)
from evolving_companion.memory_formation import (
    MemoryFormationResult,
    MemoryFormationService,
)
from evolving_companion.memory_recall import MemoryRecallResult
from evolving_companion.memory_retrieval import (
    EmbeddingEncoder,
    MemoryRetrievalCandidate,
    MemoryRetriever,
)
from evolving_companion.prompting import Message
from evolving_companion.storage import MemoryRecord, SQLiteStore


def _create_memory(store: SQLiteStore, content: str) -> MemoryRecord:
    return store.create_memory("semantic", content, "explicit", "high")


def _candidate(memory: MemoryRecord) -> MemoryRetrievalCandidate:
    return MemoryRetrievalCandidate(
        memory_id=memory.id,
        content=memory.content,
        memory_type=memory.memory_type,
        source=memory.source,
        salience=memory.salience,
        created_at=memory.created_at,
        similarity=1.0,
    )


class FakeRetriever:
    def __init__(self, candidates: list[MemoryRetrievalCandidate]) -> None:
        self.candidates = candidates
        self.calls: list[tuple[str, int]] = []

    def retrieve(self, query: str, top_n: int = 10) -> list[MemoryRetrievalCandidate]:
        self.calls.append((query, top_n))
        return self.candidates[:top_n]


class FakeJudge:
    def __init__(
        self,
        decisions: Mapping[str, ConsolidationDecision],
        *,
        fail_for: str | None = None,
    ) -> None:
        self.decisions = decisions
        self.fail_for = fail_for
        self.calls: list[tuple[str, str]] = []

    def judge(
        self, new_memory: MemoryRecord, old_memory: MemoryRecord
    ) -> ConsolidationJudgment:
        self.calls.append((new_memory.id, old_memory.id))
        if old_memory.id == self.fail_for:
            raise RuntimeError("judge unavailable")
        return ConsolidationJudgment(
            decision=self.decisions[old_memory.id], reason="fake judgment"
        )


class FakeClient(ExtractionTextCompletionClient):
    def __init__(self, response: str) -> None:
        self.response = response
        self.requests: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.requests.append(messages)
        return self.response


def test_judge_prompt_is_conservative_and_schema_is_strict() -> None:
    old = MemoryRecord(
        id=str(uuid4()),
        memory_type="semantic",
        content="用户计划以后买 Mac。",
        source="explicit",
        salience="high",
        status="active",
        created_at="2026-01-01T00:00:00+00:00",
        last_recalled_at=None,
        supersedes_memory_id=None,
    )
    new = MemoryRecord(
        **{**old.__dict__, "id": str(uuid4()), "content": "用户取消购买 Mac。"}
    )
    client = FakeClient('{"decision":"supersede_old","reason":"明确取消"}')

    judgment = MemoryConsolidationJudge(client).judge(new, old)

    assert judgment.decision == "supersede_old"
    assert "new_memory" in client.requests[0][1]["content"]
    assert (
        "仅当 new_memory 明确表示 old_memory 已不再是当前有效状态"
        in (client.requests[0][0]["content"])
    )
    with pytest.raises(ValueError):
        ConsolidationJudgment.model_validate(
            {"decision": "merge", "reason": "不允许", "summary": "额外事实"}
        )


def test_explicit_cancellation_supersedes_and_preserves_old_history(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "cancel.db")
    archive_id = store.append_archive_message(str(uuid4()), "user", "我以前计划买 Mac")
    old = store.create_memory(
        "semantic",
        "用户以后想买 Mac 做剪辑",
        "explicit",
        "high",
        evidence_refs=[archive_id],
    )
    new = _create_memory(store, "用户现在不打算买 Mac 了")
    retriever = FakeRetriever([_candidate(new), _candidate(old)])
    service = MemoryConsolidationService(
        store, retriever, FakeJudge({old.id: "supersede_old"})
    )

    result = service.process_new_memory(new.id)

    assert result.candidate_old_ids == (old.id,)
    assert result.superseded_ids == (old.id,)
    assert store.get_memory(old.id) == MemoryRecord(
        **{**old.__dict__, "status": "superseded"}
    )
    assert store.get_memory(new.id) == new
    assert store.get_evidence(old.id)[0].evidence_ref == archive_id
    assert store.get_superseded_memories(new.id) == (store.get_memory(old.id),)
    assert retriever.calls == [(new.content, 6)]


def test_explicit_preference_change_can_supersede_old_preference(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "preference.db")
    old = _create_memory(store, "用户喜欢全糖奶茶")
    new = _create_memory(store, "用户现在不喜欢太甜的奶茶")
    result = MemoryConsolidationService(
        store,
        FakeRetriever([_candidate(new), _candidate(old)]),
        FakeJudge({old.id: "supersede_old"}),
    ).process_new_memory(new.id)

    assert result.superseded_ids == (old.id,)
    assert store.get_memory(old.id).status == "superseded"
    assert store.get_memory(new.id).status == "active"


@pytest.mark.parametrize(
    ("old_content", "new_content"),
    [
        ("用户喜欢策略游戏", "用户也喜欢剧情游戏"),
        ("用户研究 SciML", "用户研究 PINN"),
        ("用户喜欢咖啡", "用户今天不想喝咖啡"),
    ],
)
def test_supplement_specificity_and_temporary_state_keep_both(
    tmp_path: Path, old_content: str, new_content: str
) -> None:
    store = SQLiteStore(tmp_path / f"keep-{uuid4()}.db")
    old = _create_memory(store, old_content)
    new = _create_memory(store, new_content)
    service = MemoryConsolidationService(
        store,
        FakeRetriever([_candidate(new), _candidate(old)]),
        FakeJudge({old.id: "keep_both"}),
    )

    result = service.process_new_memory(new.id)

    assert result.decisions[0].decision == "keep_both"
    assert store.get_memory(old.id) == old
    assert store.get_memory(new.id) == new
    assert store.get_superseded_memories(new.id) == ()


def test_uncertain_does_not_supersede(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "uncertain.db")
    old = _create_memory(store, "用户喜欢喝咖啡")
    new = _create_memory(store, "用户最近好像没那么喜欢咖啡")
    service = MemoryConsolidationService(
        store,
        FakeRetriever([_candidate(old), _candidate(new)]),
        FakeJudge({old.id: "uncertain"}),
    )

    result = service.process_new_memory(new.id)

    assert result.uncertain_ids == (old.id,)
    assert store.get_memory(old.id) == old
    assert store.get_memory(new.id) == new


def test_one_new_memory_can_supersede_multiple_old_memories(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "multiple.db")
    old_records = [
        _create_memory(store, "用户计划买 MacBook Air"),
        _create_memory(store, "用户考虑买 Mac 做剪辑"),
    ]
    new = _create_memory(store, "用户已决定不购买 Mac")
    result = MemoryConsolidationService(
        store,
        FakeRetriever([_candidate(new), *map(_candidate, old_records)]),
        FakeJudge({item.id: "supersede_old" for item in old_records}),
    ).process_new_memory(new.id)

    assert set(result.superseded_ids) == {item.id for item in old_records}
    assert all(store.get_memory(item.id).status == "superseded" for item in old_records)
    assert store.get_memory(new.id) == new
    assert {item.id for item in store.get_superseded_memories(new.id)} == {
        item.id for item in old_records
    }


def test_judge_failure_leaves_all_old_memories_unchanged(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "judge-failure.db")
    old_records = [
        _create_memory(store, "用户计划买 MacBook Air"),
        _create_memory(store, "用户考虑买 Mac 做剪辑"),
    ]
    new = _create_memory(store, "用户决定不买 Mac")
    judge = FakeJudge({old_records[0].id: "supersede_old"}, fail_for=old_records[1].id)

    result = MemoryConsolidationService(
        store,
        FakeRetriever([*map(_candidate, old_records), _candidate(new)]),
        judge,
    ).process_new_memory(new.id)

    assert result.error == "RuntimeError"
    assert store.get_memory(new.id) == new
    assert all(store.get_memory(item.id) == item for item in old_records)
    assert store.get_superseded_memories(new.id) == ()


def test_retrieval_failure_leaves_new_and_old_memories_unchanged(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "retrieval-failure.db")
    old = _create_memory(store, "用户喜欢咖啡")
    new = _create_memory(store, "用户现在不喝咖啡")

    class FailingRetriever:
        def retrieve(self, query: str, top_n: int = 10):
            raise RuntimeError("retrieval unavailable")

    result = MemoryConsolidationService(
        store, FailingRetriever(), FakeJudge({old.id: "supersede_old"})
    ).process_new_memory(new.id)

    assert result.error == "RuntimeError"
    assert store.get_memory(new.id) == new
    assert store.get_memory(old.id) == old


def test_consolidation_failure_does_not_fail_conversation_or_undo_new_memory(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "conversation-failure.db")
    old = _create_memory(store, "用户以后想买 Mac 做剪辑")
    new_content = "用户现在不打算买 Mac 了"

    class Extractor:
        def extract_memories(
            self, messages: Sequence[ArchiveChunkMessage | Mapping[str, str]]
        ) -> MemoryExtractionResult:
            chunk = tuple(ArchiveChunkMessage.model_validate(item) for item in messages)
            return MemoryExtractionResult(
                candidates=[
                    MemoryCandidate(
                        content=new_content,
                        memory_type="semantic",
                        source="explicit",
                        salience="high",
                        decision="save",
                        evidence_refs=[chunk[0].id],
                        reason="明确取消计划",
                    )
                ]
            )

    class MainClient(TextCompletionClient):
        def complete(self, messages: list[Message]) -> str:
            return "回复仍然正常。"

    class FailingRetriever:
        def retrieve(self, query: str, top_n: int = 10):
            raise RuntimeError("retrieval unavailable")

    consolidation = MemoryConsolidationService(
        store, FailingRetriever(), FakeJudge({old.id: "supersede_old"})
    )
    conversation = Conversation(
        MainClient(),
        ProjectedCharacterContext("玲"),
        store,
        memory_formation_service=MemoryFormationService(
            Extractor(), store, consolidation
        ),
    )

    assert conversation.send("我现在不打算买 Mac 了") == "回复仍然正常。"

    assert store.get_memory(old.id) == old
    formation_result = conversation.last_memory_formation_result
    assert formation_result is not None
    assert len(formation_result.saved_memory_ids) == 1
    new_memory = store.get_memory(formation_result.saved_memory_ids[0])
    assert new_memory is not None and new_memory.status == "active"
    assert formation_result.consolidation_results[0].error == "RuntimeError"


def test_supersede_status_and_relationship_update_are_atomic(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "atomic.db")
    active = _create_memory(store, "活跃旧记忆")
    archived = store.create_memory(
        "semantic", "已归档旧记忆", "explicit", "low", status="archived"
    )
    new = _create_memory(store, "新记忆")

    with pytest.raises(ValueError, match="active"):
        store.supersede_memories([active.id, archived.id], new.id)

    assert store.get_memory(active.id) == active
    assert store.get_superseded_memories(new.id) == ()


class ConstantEmbedding(EmbeddingEncoder):
    def encode(self, texts: str | Sequence[str]) -> np.ndarray:
        items = [texts] if isinstance(texts, str) else list(texts)
        vectors = np.zeros((len(items), EMBEDDING_DIMENSIONS), dtype=np.float32)
        vectors[:, 0] = 1.0
        return vectors


def test_production_retriever_excludes_superseded_memories(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "active-retrieval.db")
    old = _create_memory(store, "用户以前计划买 Mac")
    new = _create_memory(store, "用户现在不买 Mac")
    store.supersede_memories([old.id], new.id)

    retrieved = MemoryRetriever(store, ConstantEmbedding()).retrieve("Mac")

    assert [item.memory_id for item in retrieved] == [new.id]
    assert old.id not in {item.memory_id for item in retrieved}


def test_consolidation_occurs_after_recall_and_response(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "turn-order.db")
    old = _create_memory(store, "用户以后想买 Mac 做剪辑")
    new_content = "用户现在不打算买 Mac 了"
    archive_id = store.append_archive_message(str(uuid4()), "user", new_content)

    class Extractor:
        def extract_memories(
            self, messages: Sequence[ArchiveChunkMessage | Mapping[str, str]]
        ) -> MemoryExtractionResult:
            chunk = tuple(ArchiveChunkMessage.model_validate(item) for item in messages)
            return MemoryExtractionResult(
                candidates=[
                    MemoryCandidate(
                        content=new_content,
                        memory_type="semantic",
                        source="explicit",
                        salience="high",
                        decision="save",
                        evidence_refs=[chunk[0].id],
                        reason="明确取消计划",
                    )
                ]
            )

    class Recall:
        def __init__(self) -> None:
            self.old_status_at_recall: str | None = None

        def recall(self, current_user_message: str, recent_messages):
            self.old_status_at_recall = store.get_memory(old.id).status
            return MemoryRecallResult(
                memory_needed=True,
                need_reason="test",
                matched_rules=("test",),
                query_mode="current_only",
                retrieval_query=current_user_message,
                candidates=(),
                recalled_memories=(),
            )

    class MainClient(TextCompletionClient):
        def complete(self, messages: list[Message]) -> str:
            assert store.get_memory(old.id).status == "active"
            return "明白。"

    new_archive_ids: list[str] = []

    class CapturingExtractor:
        def extract_memories(self, messages):
            chunk = tuple(ArchiveChunkMessage.model_validate(item) for item in messages)
            new_archive_ids.extend(message.id for message in chunk)
            return Extractor().extract_memories(chunk)

    recall = Recall()
    retriever = FakeRetriever([_candidate(old)])
    consolidation = MemoryConsolidationService(
        store, retriever, FakeJudge({old.id: "supersede_old"})
    )
    conversation = Conversation(
        MainClient(),
        ProjectedCharacterContext("玲"),
        store,
        memory_recall_service=recall,
        memory_formation_service=MemoryFormationService(
            CapturingExtractor(), store, consolidation
        ),
    )

    assert conversation.send(new_content) == "明白。"

    assert recall.old_status_at_recall == "active"
    assert store.get_memory(old.id).status == "superseded"
    assert store.list_active_memories()[-1].content == new_content
    assert archive_id not in new_archive_ids


def test_consolidation_smoke_handles_no_saved_memory(
    capsys: pytest.CaptureFixture[str],
) -> None:
    script_path = (
        Path(__file__).parents[1] / "scripts" / "run_memory_consolidation_smoke.py"
    )
    saved_memory_id = run_path(str(script_path))["_saved_memory_id"]

    assert saved_memory_id(None) is None
    assert saved_memory_id(MemoryFormationResult()) is None
    assert (
        capsys.readouterr().out.count(
            "No new memory persisted; consolidation skipped for this case."
        )
        == 2
    )
