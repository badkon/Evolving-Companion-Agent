import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from evolving_companion.memory_extraction import (
    MEMORY_EXTRACTION_PROMPT,
    ArchiveChunkMessage,
    Decision,
    MemoryCandidate,
    MemoryExtractionResult,
    MemoryExtractor,
    MemorySource,
    MemoryType,
    Salience,
    TextCompletionClient,
    persist_saved_candidates,
)
from evolving_companion.storage import SQLiteStore

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "memory_extraction_cases.json"


class FakeLLMClient(TextCompletionClient):
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self.response


def make_candidate(
    evidence_refs: list[str],
    *,
    content: str = "用户生日是 10 月 7 日",
    memory_type: MemoryType = "semantic",
    source: MemorySource = "explicit",
    salience: Salience = "high",
    decision: Decision = "save",
    reason: str = "明确且稳定的个人事实",
) -> MemoryCandidate:
    return MemoryCandidate(
        content=content,
        memory_type=memory_type,
        source=source,
        salience=salience,
        decision=decision,
        evidence_refs=evidence_refs,
        reason=reason,
    )


def make_chunk(store: SQLiteStore, *contents: str) -> list[dict[str, str]]:
    conversation_id = str(uuid4())
    return [
        {
            "id": store.append_archive_message(conversation_id, "user", content),
            "role": "user",
            "content": content,
        }
        for content in contents
    ]


def memory_count(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT count(*) FROM memories").fetchone()[0]


def test_extractor_makes_one_call_and_validates_structured_json() -> None:
    message_id = str(uuid4())
    response = json.dumps(
        {
            "candidates": [
                {
                    "content": "用户生日是 10 月 7 日",
                    "memory_type": "semantic",
                    "source": "explicit",
                    "salience": "high",
                    "decision": "save",
                    "evidence_refs": [message_id],
                    "reason": "明确且稳定的个人事实",
                }
            ]
        },
        ensure_ascii=False,
    )
    client = FakeLLMClient(response)

    result = MemoryExtractor(client).extract_memories(
        [{"id": message_id, "role": "user", "content": "我生日是 10 月 7 日。"}]
    )

    assert len(client.calls) == 1
    assert result.candidates[0].decision == "save"
    assert result.candidates[0].evidence_refs == [message_id]
    assert message_id in client.calls[0][1]["content"]


def test_candidate_limit_and_empty_result_validation() -> None:
    assert MemoryExtractionResult(candidates=[]).candidates == []
    candidate = make_candidate([str(uuid4())])
    assert MemoryExtractionResult(candidates=[candidate]).candidates == [candidate]

    with pytest.raises(ValidationError):
        MemoryExtractionResult(candidates=[candidate] * 6)
    with pytest.raises(ValidationError):
        MemoryCandidate(
            content="  ",
            memory_type="semantic",
            source="explicit",
            salience="high",
            decision="save",
            evidence_refs=[],
            reason="invalid blank content",
        )

    valid_candidate = {
        "content": "一条记忆",
        "memory_type": "semantic",
        "source": "explicit",
        "salience": "high",
        "decision": "save",
        "evidence_refs": [str(uuid4())],
        "reason": "明确事实",
    }
    for field, invalid_value in (
        ("memory_type", "preference"),
        ("source", "guessed"),
        ("salience", "urgent"),
        ("decision", "maybe"),
    ):
        with pytest.raises(ValidationError):
            MemoryCandidate.model_validate(valid_candidate | {field: invalid_value})


def test_prompt_sets_high_precision_rules_and_three_neutral_decision_examples() -> None:
    assert "只根据提供的对话内容判断" in MEMORY_EXTRACTION_PROMPT
    assert "不得补充对话中未出现的信息" in MEMORY_EXTRACTION_PROMPT
    assert "Developer 或 System 后台信息不是 Character 的 Lived Memory" in (
        MEMORY_EXTRACTION_PROMPT
    )
    assert "observed：对话中实际发生并可直接观察的事实或行为，只写“发生了什么”" in (
        MEMORY_EXTRACTION_PROMPT
    )
    assert "inferred：根据一个或多个线索推断出的认识" in MEMORY_EXTRACTION_PROMPT
    assert "明确更新或撤回旧信息" in MEMORY_EXTRACTION_PROMPT
    assert "寒暄、临时情绪或短期状态、普通一次性日常细节通常 reject" in (
        MEMORY_EXTRACTION_PROMPT
    )
    assert "最多返回 5 条" in MEMORY_EXTRACTION_PROMPT
    assert MEMORY_EXTRACTION_PROMPT.count("→") == 3


def test_save_requires_evidence_from_current_chunk(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    store = SQLiteStore(path)
    chunk = make_chunk(store, "我生日是 10 月 7 日。")

    without_evidence = MemoryExtractionResult(candidates=[make_candidate([])])
    with_external_evidence = MemoryExtractionResult(
        candidates=[make_candidate([chunk[0]["id"], str(uuid4())])]
    )

    assert persist_saved_candidates(without_evidence, chunk, store) == ()
    assert persist_saved_candidates(with_external_evidence, chunk, store) == ()
    assert memory_count(path) == 0


def test_reject_and_uncertain_candidates_are_not_persisted(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    store = SQLiteStore(path)
    chunk = make_chunk(store, "我现在困死了。", "最近好像不太喜欢这个游戏了。")
    result = MemoryExtractionResult(
        candidates=[
            make_candidate([chunk[0]["id"]], decision="reject"),
            make_candidate([chunk[1]["id"]], decision="uncertain"),
        ]
    )

    assert persist_saved_candidates(result, chunk, store) == ()
    assert memory_count(path) == 0


def test_save_persists_memory_and_all_evidence_without_reason(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    store = SQLiteStore(path)
    chunk = make_chunk(store, "我生日是 10 月 7 日。", "每年都在这天过生日。")
    result = MemoryExtractionResult(
        candidates=[
            make_candidate(
                [message["id"] for message in chunk],
                reason="两条明确证据共同支持",
            )
        ]
    )

    saved = persist_saved_candidates(result, chunk, store)

    assert len(saved) == 1
    assert store.get_memory(saved[0].id) == saved[0]
    assert [evidence.evidence_ref for evidence in store.get_evidence(saved[0].id)] == [
        message["id"] for message in chunk
    ]
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(memories)")}
    assert "reason" not in columns


def test_exact_and_normalized_active_duplicates_are_not_inserted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.db"
    store = SQLiteStore(path)
    chunk = make_chunk(store, "已确认的生日")
    store.create_memory("semantic", "生日是 １０ 月 ７ 日", "explicit", "high")
    result = MemoryExtractionResult(
        candidates=[
            make_candidate([chunk[0]["id"]], content=" 生日是 10 月 7 日 "),
            make_candidate([chunk[0]["id"]], content="新偏好是轻音乐"),
            make_candidate([chunk[0]["id"]], content="新偏好是轻音乐"),
        ]
    )

    saved = persist_saved_candidates(result, chunk, store)

    assert len(saved) == 1
    assert saved[0].content == "新偏好是轻音乐"
    assert memory_count(path) == 2


def test_lightweight_benchmark_fixture_covers_requested_semantics() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = fixture["cases"]
    names = {case["name"] for case in cases}

    assert len(cases) == 12
    assert {
        "explicit_birthday",
        "long_term_purchase_plan",
        "explicit_long_term_preference",
        "temporary_sleepiness",
        "one_off_dinner",
        "greeting",
        "uncertain_preference_change",
        "explicit_preference_retraction",
        "relationship_boundary_event",
        "character_real_important_experience",
        "nonexistent_childhood",
        "fact_supported_by_multiple_messages",
    } == names
    for case in cases:
        assert case["expected_decision"] in {"save", "reject", "uncertain"}
        assert all(
            ArchiveChunkMessage.model_validate(message) for message in case["messages"]
        )
