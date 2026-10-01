from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from evolving_companion.character_projection import ProjectedCharacterContext
from evolving_companion.conversation import Conversation
from evolving_companion.prompting import Message
from evolving_companion.qq_adapter import QQPrivateChatAdapter, _parse_private_message
from evolving_companion.storage import SQLiteStore


def private_event(**changes: object) -> dict[str, object]:
    return {
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "user_id": 101,
        "message_id": 1,
        "raw_message": "早上好",
        **changes,
    }


class FakeConversation:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.fail = fail

    def send(self, user_message: str) -> str:
        self.calls.append(user_message)
        if self.fail:
            raise RuntimeError("private provider error")
        return "早上好。"


def adapter_for(core: FakeConversation, capacity: int = 1000) -> QQPrivateChatAdapter:
    return QQPrivateChatAdapter(
        core, allowed_user_ids={101}, bot_user_id=202, dedup_capacity=capacity
    )


def test_private_reply_passes_through_exactly_once() -> None:
    core = FakeConversation()
    adapter = adapter_for(core)
    result = adapter.handle_event(private_event())
    assert result.status == "handled" and result.response is not None
    assert result.response.text == "早上好。"
    assert result.response.recipient_id == "101"
    assert result.response.reply_to_message_id == "1"
    assert adapter.handle_event(private_event(message_id="1")).status == "duplicate"
    assert core.calls == ["早上好"]


@pytest.mark.parametrize(
    ("changes", "status"),
    [
        ({"message_type": "group"}, "ignored"),
        ({"user_id": 303}, "unauthorized"),
        ({"user_id": 202}, "ignored"),
        ({"post_type": "notice"}, "ignored"),
        ({"post_type": "request"}, "ignored"),
        ({"post_type": "message_sent"}, "ignored"),
        ({"sub_type": "group"}, "ignored"),
        ({"sub_type": "other"}, "ignored"),
        ({"sub_type": None}, "ignored"),
    ],
)
def test_filtered_event_never_calls_core(
    changes: dict[str, object], status: str
) -> None:
    core = FakeConversation()
    adapter = adapter_for(core)
    assert adapter.handle_event(private_event(**changes)).status == status
    assert core.calls == []
    # Rejected events must not reserve an authorized user's message ID.
    assert adapter.handle_event(private_event()).status == "handled"


@pytest.mark.parametrize(
    "event",
    [
        None,
        [],
        {},
        private_event(user_id=True),
        private_event(user_id=0),
        private_event(message_id=[]),
        private_event(raw_message=None),
        private_event(raw_message="  "),
        private_event(raw_message="[CQ:image,file=x]"),
        private_event(time="invalid"),
        private_event(time=True),
        private_event(time=-1),
        private_event(time=10**100),
    ],
)
def test_malformed_events_are_safe(event: object) -> None:
    core = FakeConversation()
    result = adapter_for(core).handle_event(event)
    assert result.status == "ignored" and result.response is None
    assert core.calls == []


def test_parsing_projects_only_needed_fields_and_utc_time() -> None:
    event = private_event(time=0, sender={"nickname": "not passed"}, group_id=999)
    event.pop("raw_message")
    event["text"] = " 保留文本 "
    message = _parse_private_message(event)
    assert message.platform == "qq" and message.conversation_type == "private"
    assert message.sender_id == "101" and message.text == " 保留文本 "
    assert message.occurred_at == datetime(1970, 1, 1, tzinfo=timezone.utc)
    assert _parse_private_message(private_event()).occurred_at is None


def test_failed_core_does_not_create_reply_or_retry() -> None:
    core = FakeConversation(fail=True)
    adapter = adapter_for(core)
    result = adapter.handle_event(private_event())
    assert result.status == "failed" and result.response is None
    assert result.reason == "conversation_failed"
    assert "private provider error" not in repr(result)
    assert adapter.handle_event(private_event()).status == "duplicate"
    assert core.calls == ["早上好"]


def test_cache_is_bounded_fifo() -> None:
    core = FakeConversation()
    adapter = adapter_for(core, 2)
    for message_id in (1, 2, 3):
        assert (
            adapter.handle_event(private_event(message_id=message_id)).status
            == "handled"
        )
    assert adapter.handle_event(private_event(message_id=2)).status == "duplicate"
    assert adapter.handle_event(private_event(message_id=1)).status == "handled"
    assert len(core.calls) == 4


def test_empty_allowlist_and_self_even_if_allowed() -> None:
    core = FakeConversation()
    empty = QQPrivateChatAdapter(core, allowed_user_ids=(), bot_user_id=202)
    assert empty.handle_event(private_event()).status == "unauthorized"
    self_allowed = QQPrivateChatAdapter(core, allowed_user_ids={202}, bot_user_id=202)
    assert self_allowed.handle_event(private_event(user_id=202)).status == "ignored"
    assert core.calls == []


@pytest.mark.parametrize("capacity", [0, -1])
def test_invalid_cache_configuration_is_rejected(capacity: int) -> None:
    with pytest.raises(ValueError):
        adapter_for(FakeConversation(), capacity)


def test_real_core_boundary_has_no_adapter_memory_or_developer_mutation(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "adapter.db")

    class Client:
        calls = 0

        def complete(self, messages: list[Message]) -> str:
            self.calls += 1
            assert messages[-1]["content"] == "/state energy low"
            assert "group_id" not in messages[-1]["content"]
            return "早上好。"

    client = Client()
    core = Conversation(client, ProjectedCharacterContext("玲"), store)
    adapter = QQPrivateChatAdapter(core, allowed_user_ids={101}, bot_user_id=202)
    unauthorized = adapter.handle_event(private_event(user_id=303))
    assert unauthorized.status == "unauthorized"
    with sqlite3.connect(store.path) as connection:
        assert (
            connection.execute("SELECT count(*) FROM archive_messages").fetchone()[0]
            == 0
        )
    event = private_event(raw_message="/state energy low", group_id=999)
    assert adapter.handle_event(event).status == "handled"
    assert adapter.handle_event(event).status == "duplicate"
    assert client.calls == 1
    assert len(core.history) == 2
    with sqlite3.connect(store.path) as connection:
        assert (
            connection.execute("SELECT count(*) FROM archive_messages").fetchone()[0]
            == 2
        )
        for table in (
            "memories",
            "character_state",
            "character_life_context",
            "world_entities",
        ):
            assert (
                connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
            )
