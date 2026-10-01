"""Coordinate one conversation turn while keeping history in memory."""

from collections.abc import Mapping
from typing import Protocol
from uuid import UUID, uuid4

from evolving_companion.character_projection import ProjectedCharacterContext
from evolving_companion.character_state import CharacterStateService
from evolving_companion.character_life import CharacterLifeService
from evolving_companion.character_state_transition import (
    CharacterStateTransitionService,
)
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
from evolving_companion.clock import Clock, SystemClock
from evolving_companion.time_model import CharacterTimeService
from evolving_companion.world import WorldEntityService


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
        character_timezone: str | None = None,
        clock: Clock | None = None,
        character_life_service: CharacterLifeService | None = None,
    ) -> None:
        if character_state_service is not None and character_id is None:
            raise ValueError(
                "character_state_service and internal character_id are required together"
            )
        if character_life_service is not None and character_id is None:
            raise ValueError("character_life_service requires identity.internal_id")
        if (
            character_id is not None
            and character_state_service is None
            and character_timezone is None
            and character_life_service is None
        ):
            raise ValueError(
                "character_id requires State or Character Time configuration"
            )
        if character_id is not None and not isinstance(character_id, UUID):
            raise TypeError("character_id must be identity.internal_id (UUID)")
        self._llm_client = llm_client
        self._clock = clock or SystemClock()
        self._prompt_builder = PromptBuilder(character_context)
        self._archive_store = archive_store
        self._memory_recall_service = memory_recall_service
        self._memory_formation_service = memory_formation_service
        self._character_state_service = character_state_service
        self._character_life_service = character_life_service
        self._state_transition_service = (
            CharacterStateTransitionService(character_state_service, self._clock)
            if character_state_service is not None
            else None
        )
        self._character_id = character_id
        self._time_service = (
            CharacterTimeService(
                archive_store, character_id, character_timezone, self._clock
            )
            if character_id is not None and character_timezone is not None
            else None
        )
        self._last_memory_formation_result: MemoryFormationResult | None = None
        self._last_interaction_error: str | None = None
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

    @property
    def last_interaction_error(self) -> str | None:
        """Return only the metadata error type for the most recent completed turn."""
        return self._last_interaction_error

    def send(self, user_message: str) -> str:
        user_archive_id = self._archive_store.append_archive_message(
            self.conversation_id, "user", user_message
        )
        character_state = None
        now_utc = self._clock.now_utc()
        if self._state_transition_service is not None:
            # character_id is validated as the stable identity.internal_id UUID.
            assert self._character_id is not None
            character_state = self._state_transition_service.apply_elapsed_time(
                self._character_id, now_utc
            ).after_state
        time_snapshot = (
            self._time_service.snapshot(now_utc)
            if self._time_service is not None
            else None
        )
        life_context = None
        if self._character_life_service is not None:
            assert self._character_id is not None
            life_context = self._character_life_service.project_context(
                self._character_id, WorldEntityService(self._archive_store, self._clock)
            )
        recalled_memories: tuple[MemoryPromptCandidate, ...] = ()
        if self._memory_recall_service is not None:
            recall_result = self._memory_recall_service.recall(
                user_message, self._history
            )
            recalled_memories = recall_result.recalled_memories
        messages = self._prompt_builder.build(
            self._history,
            user_message,
            recalled_memories,
            character_state,
            time_snapshot,
            life_context,
        )
        reply = self._llm_client.complete(messages)
        assistant_archive_id = self._archive_store.append_archive_message(
            self.conversation_id, "assistant", reply
        )
        # Successful assistant archival is the core completion boundary.
        self._history.extend(
            (
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": reply},
            )
        )
        self._last_interaction_error = None
        if self._time_service is not None:
            try:
                self._time_service.record_successful_interaction(self._clock.now_utc())
            except Exception as error:
                # Metadata failure must not invalidate an archived reply or history.
                self._last_interaction_error = type(error).__name__
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
