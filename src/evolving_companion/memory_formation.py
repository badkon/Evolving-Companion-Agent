"""Best-effort formation of memories from one completed conversation turn."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

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


@dataclass(frozen=True)
class MemoryFormationResult:
    """Internal diagnostics for one post-response extraction attempt."""

    candidates: tuple[MemoryCandidate, ...] = ()
    saved_memory_ids: tuple[str, ...] = ()
    rejected_count: int = 0
    uncertain_count: int = 0
    error: str | None = None


class MemoryFormationService:
    """Extract and persist only evidence-backed candidates from one turn pair."""

    def __init__(self, extractor: MemoryExtractionPort, store: SQLiteStore) -> None:
        self._extractor = extractor
        self._store = store

    def process_turn(
        self,
        user_archive_message: Mapping[str, str],
        assistant_archive_message: Mapping[str, str],
    ) -> MemoryFormationResult:
        chunk = (
            ArchiveChunkMessage.model_validate(user_archive_message),
            ArchiveChunkMessage.model_validate(assistant_archive_message),
        )
        if chunk[0].role != "user" or chunk[1].role != "assistant":
            raise ValueError("memory formation requires a user/assistant turn pair")

        result = self._extractor.extract_memories(chunk)
        saved = persist_saved_candidates(result, chunk, self._store)
        return MemoryFormationResult(
            candidates=tuple(result.candidates),
            saved_memory_ids=tuple(memory.id for memory in saved),
            rejected_count=sum(
                candidate.decision == "reject" for candidate in result.candidates
            ),
            uncertain_count=sum(
                candidate.decision == "uncertain" for candidate in result.candidates
            ),
        )
