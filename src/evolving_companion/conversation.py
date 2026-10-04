"""Coordinate one conversation turn while keeping history in memory."""

from collections.abc import Mapping
from functools import partial
import logging
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
    AffectiveFormationPort,
)
from evolving_companion.memory_recall import MemoryRecallPort
from evolving_companion.observation import ObservationContextPort
from evolving_companion.prompting import (
    MemoryPromptCandidate,
    Message,
    PromptBuilder,
)
from evolving_companion.storage import SQLiteStore
from evolving_companion.clock import Clock, SystemClock
from evolving_companion.time_model import CharacterTimeService
from evolving_companion.world import WorldEntityService
from evolving_companion.reply_pipeline import NaturalReplyPipeline
from evolving_companion.reply_planning import ReplyTarget
from evolving_companion.affective import Event


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
        observation_service: ObservationContextPort | None = None,
        reply_pipeline: NaturalReplyPipeline | None = None,
    ) -> None:
        if character_state_service is not None and character_id is None:
            raise ValueError(
                "character_state_service and internal character_id are required together"
            )
        if character_life_service is not None and character_id is None:
            raise ValueError("character_life_service requires identity.internal_id")
        if observation_service is not None and character_id is None:
            raise ValueError("observation_service requires identity.internal_id")
        if (
            character_id is not None
            and character_state_service is None
            and character_timezone is None
            and character_life_service is None
            and observation_service is None
        ):
            raise ValueError(
                "character_id requires State or Character Time configuration"
            )
        if character_id is not None and not isinstance(character_id, UUID):
            raise TypeError("character_id must be identity.internal_id (UUID)")
        self._llm_client = llm_client
        self._reply_pipeline = reply_pipeline
        self._clock = clock or SystemClock()
        self._prompt_builder = PromptBuilder(character_context)
        self._archive_store = archive_store
        self._memory_recall_service = memory_recall_service
        self._memory_formation_service = memory_formation_service
        self._character_state_service = character_state_service
        self._character_life_service = character_life_service
        self._observation_service = observation_service
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
        self._last_memory_recall_error: str | None = None
        self._last_observation_error: str | None = None
        self.conversation_id = str(uuid4())
        self._history: list[Message] = []
        self._relationship_target = (
            reply_pipeline.affective_store.primary_target
            if reply_pipeline is not None and reply_pipeline.affective_store is not None
            else None
        )
        self._target_sessions = {
            self._relationship_target: (self.conversation_id, self._history)
        }

    def send_for(self, user_message: str, target_id: UUID) -> str:
        """Transport-validated person key; separate history, shared Character affect."""
        if not isinstance(target_id, UUID):
            raise TypeError("Relationship target must be a UUID")
        if self._reply_pipeline is None or self._reply_pipeline.affective_store is None:
            return self.send(user_message)
        previous = self._relationship_target, self.conversation_id, self._history
        self._relationship_target = target_id
        self.conversation_id, self._history = self._target_sessions.setdefault(
            target_id, (str(uuid4()), [])
        )
        try:
            return self.send(user_message)
        finally:
            self._relationship_target, self.conversation_id, self._history = previous

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

    @property
    def last_memory_recall_error(self) -> str | None:
        """Developer-only exception type for this turn; never prompt content."""
        return self._last_memory_recall_error

    @property
    def last_observation_error(self) -> str | None:
        """Developer-only exception type; no cached observation or failure text."""
        return self._last_observation_error

    def send(self, user_message: str) -> str:
        # Existing Memory has no user ownership key. Do not expose or mix it across
        # newly separated relation targets; keep the primary user's path unchanged.
        memory_allowed = (
            self._reply_pipeline is None
            or self._reply_pipeline.affective_store is None
            or self._relationship_target
            == self._reply_pipeline.affective_store.primary_target
        )
        self._last_memory_recall_error = None
        self._last_observation_error = None
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
        observation = None
        if self._observation_service is not None:
            assert self._character_id is not None
            try:
                life_context, observation = self._observation_service.capture_context(
                    self._character_id, now_utc
                )
            except Exception as error:
                # Drop both contexts; do not mix separate location versions or reuse
                # an old snapshot. No exception text/traceback enters logs or Prompt.
                self._last_observation_error = type(error).__name__
                logging.getLogger(__name__).warning(
                    "observation_failed: %s", self._last_observation_error
                )
        elif self._character_life_service is not None:
            assert self._character_id is not None
            life_context = self._character_life_service.project_context(
                self._character_id, WorldEntityService(self._archive_store, self._clock)
            )
        recalled_memories: tuple[MemoryPromptCandidate, ...] = ()
        if self._memory_recall_service is not None and memory_allowed:
            try:
                recall_result = self._memory_recall_service.recall(
                    user_message, self._history
                )
                recalled_memories = recall_result.recalled_memories
            except Exception as error:
                # Recall is auxiliary context, not the main completion boundary.
                # Do not log exception text/traceback: it may contain private data.
                self._last_memory_recall_error = type(error).__name__
                logging.getLogger(__name__).warning(
                    "memory_recall_failed: %s", self._last_memory_recall_error
                )
        messages = self._prompt_builder.build(
            self._history,
            user_message,
            recalled_memories,
            character_state,
            time_snapshot,
            life_context,
            observation,
            affective_managed=self._reply_pipeline is not None
            and self._reply_pipeline.affective_store is not None,
            persona_managed=self._reply_pipeline is not None,
        )
        event = (
            Event(
                id=UUID(user_archive_id),
                type="conversation_message",
                actor=self._relationship_target,
                target=self._character_id,
                summary="收到一条用户消息；内容由本轮 Archive 提供。",
                timestamp=now_utc,
                source="archive",
            )
            if self._reply_pipeline is not None
            and self._reply_pipeline.affective_store is not None
            and self._relationship_target is not None
            and self._character_id is not None
            else None
        )
        reply = (
            self._reply_pipeline.reply(
                messages,
                ReplyTarget(user_message),
                event,
                character=self._prompt_builder.character_context,
            )
            if self._reply_pipeline is not None
            else self._llm_client.complete(messages)
        )
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
                self._time_service.record_successful_interaction(now_utc)
            except Exception as error:
                # Metadata failure must not invalidate an archived reply or history.
                self._last_interaction_error = type(error).__name__
        self._last_memory_formation_result = None
        if self._memory_formation_service is not None and memory_allowed:
            try:
                formation = self._memory_formation_service.process_turn
                hint = (
                    self._reply_pipeline.last_significant_event
                    if self._reply_pipeline is not None
                    else None
                )
                if hint is not None and isinstance(
                    self._memory_formation_service, AffectiveFormationPort
                ):
                    formation = partial(
                        self._memory_formation_service.process_turn_with_affect,
                        hint=hint,
                    )
                self._last_memory_formation_result = formation(
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
            except Exception as error:
                # Keep provider/database details out of user-visible output and logs.
                self._last_memory_formation_result = MemoryFormationResult(
                    error=type(error).__name__
                )
        return reply
