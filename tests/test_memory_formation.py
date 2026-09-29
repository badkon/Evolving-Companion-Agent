import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from uuid import uuid4

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_projection import (
    CharacterProjector,
    ProjectedCharacterContext,
)
from evolving_companion.conversation import Conversation, TextCompletionClient
from evolving_companion.memory_extraction import (
    ArchiveChunkMessage,
    MemoryCandidate,
    MemoryExtractionResult,
    Decision,
)
from evolving_companion.memory_formation import (
    MemoryFormationService,
)
from evolving_companion.memory_recall import MemoryRecallResult
from evolving_companion.memory_reranker import RerankedMemoryCandidate
from evolving_companion.prompting import Message
from evolving_companion.storage import SQLiteStore


class FakeLLMClient(TextCompletionClient):
    def __init__(self, replies: list[str | Exception]) -> None:
        self.replies = iter(replies)
        self.requests: list[list[Message]] = []

    def complete(self, messages: list[Message]) -> str:
        self.requests.append(messages)
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeExtractor:
    def __init__(self, decision: Decision = "save") -> None:
        self.decision = decision
        self.calls: list[tuple[ArchiveChunkMessage, ...]] = []

    def extract_memories(
        self, messages: Sequence[ArchiveChunkMessage | Mapping[str, str]]
    ) -> MemoryExtractionResult:
        chunk = tuple(
            ArchiveChunkMessage.model_validate(message) for message in messages
        )
        self.calls.append(chunk)
        user_message = chunk[0].content
        if self.decision != "save" or "生日" not in user_message:
            return MemoryExtractionResult(candidates=[])
        return MemoryExtractionResult(
            candidates=[
                MemoryCandidate(
                    content="用户生日是 10 月 7 日。",
                    memory_type="semantic",
                    source="explicit",
                    salience="high",
                    decision="save",
                    evidence_refs=[chunk[0].id],
                    reason="用户明确提供的稳定事实",
                )
            ]
        )


class FakeRecallService:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store
        self.memory_counts_at_recall: list[int] = []

    def recall(
        self,
        current_user_message: str,
        recent_messages: Sequence[Mapping[str, str]],
    ) -> MemoryRecallResult:
        memories = self.store.list_active_memories()
        self.memory_counts_at_recall.append(len(memories))
        recalled = tuple(
            RerankedMemoryCandidate(
                memory_id=memory.id,
                content=memory.content,
                memory_type=memory.memory_type,
                source=memory.source,
                salience=memory.salience,
                created_at=memory.created_at,
                semantic_similarity=1.0,
                raw_score=1.0,
                normalized_score=0.73,
            )
            for memory in memories
            if "生日" in current_user_message and "生日" in memory.content
        )
        return MemoryRecallResult(
            memory_needed=True,
            need_reason="fake test recall",
            matched_rules=("fake",),
            query_mode="current_only",
            retrieval_query=current_user_message,
            candidates=recalled,
            recalled_memories=recalled,
        )


def _character_context() -> ProjectedCharacterContext:
    seed_path = (
        Path(__file__).resolve().parents[1] / "data" / "characters" / "si_001.yaml"
    )
    return CharacterProjector().project(load_character_seed_data(seed_path))


def _memory_count(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT count(*) FROM memories").fetchone()[0]


def test_current_turn_memory_is_saved_after_recall_and_recalled_next_turn(
    tmp_path: Path,
) -> None:
    path = tmp_path / "formation.db"
    store = SQLiteStore(path)
    extractor = FakeExtractor()
    recall = FakeRecallService(store)
    client = FakeLLMClient(["记下啦。", "10 月 7 日。"])
    conversation = Conversation(
        client,
        _character_context(),
        store,
        memory_recall_service=recall,
        memory_formation_service=MemoryFormationService(extractor, store),
    )

    assert conversation.send("我的生日是 10 月 7 日。") == "记下啦。"
    first_system_prompt = client.requests[0][0]["content"]
    assert "【可参考的长期记忆候选】" not in first_system_prompt
    assert recall.memory_counts_at_recall == [0]
    assert _memory_count(path) == 1
    assert conversation.last_memory_formation_result is not None
    memory_id = conversation.last_memory_formation_result.saved_memory_ids[0]
    assert len(extractor.calls) == 1
    assert len(extractor.calls[0]) == 2

    assert conversation.send("我的生日是哪天？") == "10 月 7 日。"
    assert recall.memory_counts_at_recall == [0, 1]
    assert "用户生日是 10 月 7 日。" in client.requests[1][0]["content"]

    archive_ids = {message.id for message in extractor.calls[0]}
    evidence_refs = {
        evidence.evidence_ref for evidence in store.get_evidence(memory_id)
    }
    assert evidence_refs <= archive_ids


def test_duplicate_content_is_not_saved_twice(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "dedup.db")
    extractor = FakeExtractor()
    service = MemoryFormationService(extractor, store)
    conversation_id = str(uuid4())

    for content in ("我的生日是 10 月 7 日。", "我的生日是 10 月 7 日。"):
        user_id = store.append_archive_message(conversation_id, "user", content)
        assistant_id = store.append_archive_message(
            conversation_id, "assistant", "好的"
        )
        service.process_turn(
            {"id": user_id, "role": "user", "content": content},
            {"id": assistant_id, "role": "assistant", "content": "好的"},
        )

    assert len(store.list_active_memories()) == 1


def test_generic_knowledge_and_greeting_do_not_form_memory(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "ordinary-turns.db")
    extractor = FakeExtractor()
    service = MemoryFormationService(extractor, store)
    conversation_id = str(uuid4())

    for content in ("PINN 最早是哪篇论文提出的？", "早上好"):
        user_id = store.append_archive_message(conversation_id, "user", content)
        assistant_id = store.append_archive_message(
            conversation_id, "assistant", "你好"
        )
        result = service.process_turn(
            {"id": user_id, "role": "user", "content": content},
            {"id": assistant_id, "role": "assistant", "content": "你好"},
        )
        assert result.candidates == ()

    assert store.list_active_memories() == ()


def test_invalid_evidence_candidate_is_skipped(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "evidence.db")
    conversation_id = str(uuid4())
    user_id = store.append_archive_message(
        conversation_id, "user", "我的生日是 10 月 7 日"
    )
    assistant_id = store.append_archive_message(conversation_id, "assistant", "好")

    class InvalidEvidenceExtractor:
        def extract_memories(
            self, messages: Sequence[ArchiveChunkMessage | Mapping[str, str]]
        ) -> MemoryExtractionResult:
            return MemoryExtractionResult(
                candidates=[
                    MemoryCandidate(
                        content="不可保存",
                        memory_type="semantic",
                        source="explicit",
                        salience="high",
                        decision="save",
                        evidence_refs=[str(uuid4())],
                        reason="fake invalid evidence",
                    )
                ]
            )

    MemoryFormationService(InvalidEvidenceExtractor(), store).process_turn(
        {"id": user_id, "role": "user", "content": "我的生日是 10 月 7 日"},
        {"id": assistant_id, "role": "assistant", "content": "好"},
    )
    assert store.list_active_memories() == ()


def test_extraction_failure_does_not_fail_successful_reply(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "failure.db")

    class FailingExtractor:
        def extract_memories(
            self, messages: Sequence[ArchiveChunkMessage | Mapping[str, str]]
        ) -> MemoryExtractionResult:
            raise RuntimeError("provider failure detail")

    conversation = Conversation(
        FakeLLMClient(["回复成功"]),
        _character_context(),
        store,
        memory_formation_service=MemoryFormationService(FailingExtractor(), store),
    )
    assert conversation.send("我的生日是 10 月 7 日") == "回复成功"
    assert conversation.last_memory_formation_result is not None
    assert conversation.last_memory_formation_result.error == "RuntimeError"
    assert "provider failure detail" not in repr(
        conversation.last_memory_formation_result
    )
    with sqlite3.connect(store.path) as connection:
        assert (
            connection.execute("SELECT count(*) FROM archive_messages").fetchone()[0]
            == 2
        )
    assert store.list_active_memories() == ()


def test_main_llm_failure_does_not_call_extractor(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "main-failure.db")
    extractor = FakeExtractor()
    conversation = Conversation(
        FakeLLMClient([RuntimeError("main failure")]),
        _character_context(),
        store,
        memory_formation_service=MemoryFormationService(extractor, store),
    )

    try:
        conversation.send("我的生日是 10 月 7 日")
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the main model call to fail")

    assert extractor.calls == []
    with sqlite3.connect(store.path) as connection:
        rows = connection.execute("SELECT role FROM archive_messages").fetchall()
    assert rows == [("user",)]
    assert store.list_active_memories() == ()
