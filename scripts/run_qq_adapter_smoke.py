"""Entirely offline QQ adapter smoke; no database, API key or QQ connection."""

from evolving_companion.qq_adapter import QQPrivateChatAdapter


class FakeConversation:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def send(self, user_message: str) -> str:
        self.calls.append(user_message)
        return "早上好。"


def main() -> None:
    conversation = FakeConversation()
    adapter = QQPrivateChatAdapter(
        conversation, allowed_user_ids={101}, bot_user_id=202
    )
    event = {
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "user_id": 101,
        "message_id": 1,
        "raw_message": "早上好",
    }
    result = adapter.handle_event(event)
    assert result.status == "handled" and result.response is not None
    assert result.response.text == "早上好。"
    print(f"allowed: {result.status} | {result.response.text}")
    for name, payload, expected in (
        ("repeat", event, "duplicate"),
        ("stranger", event | {"user_id": 303, "message_id": 2}, "unauthorized"),
        ("group", event | {"message_type": "group"}, "ignored"),
        ("self", event | {"user_id": 202}, "ignored"),
    ):
        result = adapter.handle_event(payload)
        assert result.status == expected and result.response is None
        print(f"{name}: {result.status} | {result.reason}")
    assert conversation.calls == ["早上好"]
    print("Offline QQ adapter smoke passed; Conversation called exactly once.")


if __name__ == "__main__":
    main()
