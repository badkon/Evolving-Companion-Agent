"""Coordinate one conversation turn while keeping history in memory."""

from collections.abc import Mapping
from typing import Protocol
from uuid import UUID, uuid4

from evolving_companion.character_projection import ProjectedCharacterContext
from evolving_companion.character_state import CharacterStateService
from evolving_companion.memory_formation import (
    MemoryFormationPort,
    MemoryFormationResult,
)
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
        memory_formation_service: MemoryFormationPort | None = None,
        *,
        character_state_service: CharacterStateService | None = None,
        character_id: UUID | None = None,
    ) -> None:
        if (character_state_service is None) != (character_id is None):
            raise ValueError(
                "character_state_service and internal character_id are required together"
            )
        if character_id is not None and not isinstance(character_id, UUID):
            raise TypeError("character_id must be identity.internal_id (UUID)")
        self._llm_client = llm_client
        self._prompt_builder = PromptBuilder(character_context)
        self._archive_store = archive_store
        self._memory_recall_service = memory_recall_service
        self._memory_formation_service = memory_formation_service
        self._character_state_service = character_state_service
        self._character_id = character_id
        self._last_memory_formation_result: MemoryFormationResult | None = None
        self.conversation_id = str(uuid4())
        self._history: list[Message] = []

    @property
    def history(self) -> tuple[Mapping[str, str], ...]:
        """Return a read-only snapshot of the in-memory conversation history."""
        return tuple(dict(message) for message in self._history)

    @property
    def last_memory_formation_result(self) -> MemoryFormationResult | None:
        """Return internal diagnostics for the most recent completed turn."""
        return self._last_memory_formation_result

    def send(self, user_message: str) -> str:
        user_archive_id = self._archive_store.append_archive_message(
            self.conversation_id, "user", user_message
        )
        character_state = None
        if self._character_state_service is not None:
            # character_id is validated as the stable identity.internal_id UUID.
            assert self._character_id is not None
            character_state = self._character_state_service.get_state(
                self._character_id
            )
        recalled_memories: tuple[MemoryPromptCandidate, ...] = ()
        if self._memory_recall_service is not None:
            recall_result = self._memory_recall_service.recall(
                user_message, self._history
            )
            recalled_memories = recall_result.recalled_memories
        messages = self._prompt_builder.build(
            self._history, user_message, recalled_memories, character_state
        )
        reply = self._llm_client.complete(messages)
        assistant_archive_id = self._archive_store.append_archive_message(
            self.conversation_id, "assistant", reply
        )
        self._history.extend(
            (
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": reply},
            )
        )
        self._last_memory_formation_result = None
        if self._memory_formation_service is not None:
            try:
                self._last_memory_formation_result = (
                    self._memory_formation_service.process_turn(
                        {
                            "id": user_archive_id,
                            "role": "user",
                            "content": user_message,
                        },
                        {
                            "id": assistant_archive_id,
                            "role": "assistant",
                            "content": reply,
                        },
                    )
                )
            except Exception as error:
                # Keep provider/database details out of user-visible output and logs.
                self._last_memory_formation_result = MemoryFormationResult(
                    error=type(error).__name__
                )
        return reply
