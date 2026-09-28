"""Coordinate one conversation turn while keeping history in memory."""

from collections.abc import Mapping
from typing import Protocol
from uuid import uuid4

from evolving_companion.character_projection import ProjectedCharacterContext
from evolving_companion.prompting import Message, PromptBuilder
from evolving_companion.storage import SQLiteStore


class TextCompletionClient(Protocol):
    """The conversation layer depends on messages-to-text behavior only."""

    def complete(self, messages: list[Message]) -> str: ...


class Conversation:
    """Build prompts, request replies, and append successful turns in order."""

    def __init__(
        self,
        llm_client: TextCompletionClient,
        character_context: ProjectedCharacterContext,
        archive_store: SQLiteStore,
    ) -> None:
        self._llm_client = llm_client
        self._prompt_builder = PromptBuilder(character_context)
        self._archive_store = archive_store
        self.conversation_id = str(uuid4())
        self._history: list[Message] = []

    @property
    def history(self) -> tuple[Mapping[str, str], ...]:
        """Return a read-only snapshot of the in-memory conversation history."""
        return tuple(dict(message) for message in self._history)

    def send(self, user_message: str) -> str:
        self._archive_store.append_archive_message(
            self.conversation_id, "user", user_message
        )
        messages = self._prompt_builder.build(self._history, user_message)
        reply = self._llm_client.complete(messages)
        self._archive_store.append_archive_message(
            self.conversation_id, "assistant", reply
        )
        self._history.extend(
            (
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": reply},
            )
        )
        return reply
