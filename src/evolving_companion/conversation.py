"""Coordinate one conversation turn while keeping history in memory."""

from collections.abc import Mapping
from typing import Protocol
from uuid import uuid4

from evolving_companion.character_projection import ProjectedCharacterContext
from evolving_companion.memory_recall import MemoryRecallPort
from evolving_companion.prompting import (
    MemoryPromptCandidate,
    Message,
    PromptBuilder,
)
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
        memory_recall_service: MemoryRecallPort | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._prompt_builder = PromptBuilder(character_context)
        self._archive_store = archive_store
        self._memory_recall_service = memory_recall_service
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
        recalled_memories: tuple[MemoryPromptCandidate, ...] = ()
        if self._memory_recall_service is not None:
            recall_result = self._memory_recall_service.recall(
                user_message, self._history
            )
            recalled_memories = recall_result.recalled_memories
        messages = self._prompt_builder.build(
            self._history, user_message, recalled_memories
        )
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
