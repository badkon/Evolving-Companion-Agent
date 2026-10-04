"""Best-effort formation of memories from one completed conversation turn."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from evolving_companion.affective import SignificantAffectiveEvent

from evolving_companion.memory_consolidation import (
    MemoryConsolidationResult,
)
from evolving_companion.memory_extraction import (
    ArchiveChunkMessage,
    MemoryCandidate,
    MemoryExtractionResult,
    persist_saved_candidates,
)
from evolving_companion.storage import SQLiteStore


class MemoryExtractionPort(Protocol):
    def extract_memories(
        self, messages: Sequence[ArchiveChunkMessage | Mapping[str, str]]
    ) -> MemoryExtractionResult: ...


class MemoryFormationPort(Protocol):
    def process_turn(
        self,
        user_archive_message: Mapping[str, str],
        assistant_archive_message: Mapping[str, str],
    ) -> "MemoryFormationResult": ...


@runtime_checkable
class AffectiveFormationPort(Protocol):
    def process_turn_with_affect(
        self,
        user_archive_message: Mapping[str, str],
        assistant_archive_message: Mapping[str, str],
        hint: SignificantAffectiveEvent,
    ) -> "MemoryFormationResult": ...


@runtime_checkable
class AffectiveExtractionPort(Protocol):
    def extract_with_affect(
        self,
        messages: Sequence[ArchiveChunkMessage | Mapping[str, str]],
        hint: SignificantAffectiveEvent,
    ) -> MemoryExtractionResult: ...


class MemoryConsolidationPort(Protocol):
    def process_new_memory(self, memory_id: str) -> MemoryConsolidationResult: ...


@dataclass(frozen=True)
class MemoryFormationResult:
    """Internal diagnostics for one post-response extraction attempt."""

    candidates: tuple[MemoryCandidate, ...] = ()
    saved_memory_ids: tuple[str, ...] = ()
    rejected_count: int = 0
    uncertain_count: int = 0
    consolidation_results: tuple[MemoryConsolidationResult, ...] = ()
    error: str | None = None


class MemoryFormationService:
    """Extract and persist only evidence-backed candidates from one turn pair."""

    def __init__(
        self,
        extractor: MemoryExtractionPort,
        store: SQLiteStore,
        consolidation_service: MemoryConsolidationPort | None = None,
    ) -> None:
        self._extractor = extractor
        self._store = store
        self._consolidation_service = consolidation_service

    def process_turn(
        self,
        user_archive_message: Mapping[str, str],
        assistant_archive_message: Mapping[str, str],
    ) -> MemoryFormationResult:
        return self._process_turn(user_archive_message, assistant_archive_message)

    def process_turn_with_affect(
        self,
        user_archive_message: Mapping[str, str],
        assistant_archive_message: Mapping[str, str],
        hint: SignificantAffectiveEvent,
    ) -> MemoryFormationResult:
        return self._process_turn(user_archive_message, assistant_archive_message, hint)

    def _process_turn(
        self,
        user_archive_message: Mapping[str, str],
        assistant_archive_message: Mapping[str, str],
        hint: SignificantAffectiveEvent | None = None,
    ) -> MemoryFormationResult:
        chunk = (
            ArchiveChunkMessage.model_validate(user_archive_message),
            ArchiveChunkMessage.model_validate(assistant_archive_message),
        )
        if chunk[0].role != "user" or chunk[1].role != "assistant":
            raise ValueError("memory formation requires a user/assistant turn pair")

        result = (
            self._extractor.extract_with_affect(chunk, hint)
            if hint is not None
            and str(hint.source_event_id) == chunk[0].id
            and isinstance(self._extractor, AffectiveExtractionPort)
            else self._extractor.extract_memories(chunk)
        )
        saved = persist_saved_candidates(result, chunk, self._store)
        consolidation_results: list[MemoryConsolidationResult] = []
        if self._consolidation_service is not None:
            for memory in saved:
                try:
                    consolidation_results.append(
                        self._consolidation_service.process_new_memory(memory.id)
                    )
                except Exception as error:
                    consolidation_results.append(
                        MemoryConsolidationResult(
                            new_memory_id=memory.id,
                            error=type(error).__name__,
                        )
                    )
        return MemoryFormationResult(
            candidates=tuple(result.candidates),
            saved_memory_ids=tuple(memory.id for memory in saved),
            rejected_count=sum(
                candidate.decision == "reject" for candidate in result.candidates
            ),
            uncertain_count=sum(
                candidate.decision == "uncertain" for candidate in result.candidates
            ),
            consolidation_results=tuple(consolidation_results),
        )
