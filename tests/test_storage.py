import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from evolving_companion.character_projection import ProjectedCharacterContext
from evolving_companion.conversation import Conversation
from evolving_companion.storage import SQLiteStore


def archive_rows(path: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            "SELECT * FROM archive_messages ORDER BY rowid"
        ).fetchall()


def test_store_initializes_three_tables_in_temporary_database(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "character.db"

    SQLiteStore(path)

    assert path.is_file()
    with sqlite3.connect(path) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {
        "archive_messages",
        "memories",
        "memory_evidence",
        "memory_embeddings",
    } <= names


def test_archive_rejects_invalid_role_and_empty_content(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "character.db")
    conversation_id = str(uuid4())

    with pytest.raises(ValueError, match="role"):
        store.append_archive_message(conversation_id, "system", "指令")
    with pytest.raises(ValueError, match="content"):
        store.append_archive_message(conversation_id, "user", "  ")


def test_conversation_archives_each_message_at_the_required_time(
    tmp_path: Path,
) -> None:
    path = tmp_path / "character.db"
    store = SQLiteStore(path)

    class InspectingClient:
        def complete(self, messages: list[dict[str, str]]) -> str:
            rows = archive_rows(path)
            assert len(rows) == 1
            assert rows[0]["role"] == "user"
            assert rows[0]["content"] == " 你好 "
            assert messages[-1] == {"role": "user", "content": " 你好 "}
            return "你好。"

    conversation = Conversation(
        InspectingClient(), ProjectedCharacterContext("玲"), store
    )

    assert conversation.send(" 你好 ") == "你好。"

    rows = archive_rows(path)
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert [row["content"] for row in rows] == [" 你好 ", "你好。"]
    assert {row["conversation_id"] for row in rows} == {conversation.conversation_id}
    UUID(conversation.conversation_id)
    for row in rows:
        UUID(row["id"])
        assert datetime.fromisoformat(row["created_at"]).utcoffset() == timedelta(0)
    assert [message["role"] for message in conversation.history] == [
        "user",
        "assistant",
    ]


def test_failed_llm_call_leaves_user_archive_but_no_in_memory_turn(
    tmp_path: Path,
) -> None:
    path = tmp_path / "character.db"

    class FailingClient:
        def complete(self, messages: list[dict[str, str]]) -> str:
            raise RuntimeError("LLM unavailable")

    conversation = Conversation(
        FailingClient(), ProjectedCharacterContext("玲"), SQLiteStore(path)
    )

    with pytest.raises(RuntimeError, match="LLM unavailable"):
        conversation.send("你好")

    assert [(row["role"], row["content"]) for row in archive_rows(path)] == [
        ("user", "你好")
    ]
    assert conversation.history == ()


def test_memory_can_be_created_and_read_without_automatic_extraction(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "character.db")

    memory = store.create_memory("semantic", "用户喜欢旧书", "explicit", "medium")

    UUID(memory.id)
    assert memory.status == "active"
    assert memory.last_recalled_at is None
    assert memory.supersedes_memory_id is None
    assert datetime.fromisoformat(memory.created_at).utcoffset() == timedelta(0)
    assert store.get_memory(memory.id) == memory
    assert store.get_memory(str(uuid4())) is None


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("memory_type", "dream"),
        ("source", "fabricated"),
        ("salience", "urgent"),
        ("status", "pending"),
    ],
)
def test_invalid_memory_enums_are_rejected_in_python_and_sqlite(
    tmp_path: Path, field: str, invalid: str
) -> None:
    path = tmp_path / "character.db"
    store = SQLiteStore(path)
    values = {
        "memory_type": "episodic",
        "content": "一条记忆",
        "source": "observed",
        "salience": "low",
        "status": "active",
    }
    values[field] = invalid

    with pytest.raises(ValueError):
        store.create_memory(**values)

    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO memories
                   (id, memory_type, content, source, salience, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid4()),
                    values["memory_type"],
                    values["content"],
                    values["source"],
                    values["salience"],
                    values["status"],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )


def test_evidence_links_existing_archive_messages_and_memory(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "character.db")
    conversation_id = str(uuid4())
    first_id = store.append_archive_message(conversation_id, "user", "第一条")
    second_id = store.append_archive_message(conversation_id, "assistant", "第二条")
    memory = store.create_memory("episodic", "一次对话", "observed", "high")

    first = store.add_evidence(memory.id, "archive_message", first_id)
    second = store.add_evidence(memory.id, "archive_message", second_id)

    assert store.get_evidence(memory.id) == (first, second)
    with pytest.raises(sqlite3.IntegrityError):
        store.add_evidence(str(uuid4()), "archive_message", first_id)
    with pytest.raises(ValueError, match="existing message"):
        store.add_evidence(memory.id, "archive_message", str(uuid4()))


def test_new_memory_can_reference_a_superseded_memory(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "character.db")
    previous = store.create_memory("self", "旧记录", "explicit", "low")

    current = store.create_memory(
        "self", "新记录", "explicit", "medium", supersedes_memory_id=previous.id
    )

    assert store.get_memory(current.id) == current
    assert current.supersedes_memory_id == previous.id
    assert store.get_memory(previous.id) == previous
    with pytest.raises(sqlite3.IntegrityError):
        store.create_memory(
            "self", "无效引用", "explicit", "low", supersedes_memory_id=str(uuid4())
        )
