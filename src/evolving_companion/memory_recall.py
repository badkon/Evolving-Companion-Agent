"""Coordinate memory need, context selection, retrieval, reranking, and Top-3 recall."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from evolving_companion.memory_policy import (
    MemoryContextPolicy,
    MemoryNeedPolicy,
    QueryMode,
    build_query,
)
from evolving_companion.memory_reranker import MemoryReranker, RerankedMemoryCandidate
from evolving_companion.memory_retrieval import MemoryRetrievalCandidate

SEMANTIC_TOP_N = 10
INJECTION_TOP_K = 3
RECENT_CONTEXT_MESSAGES = 5


class MemorySearch(Protocol):
    def retrieve(
        self, query: str, top_n: int = 10
    ) -> list[MemoryRetrievalCandidate]: ...


@dataclass(frozen=True)
class MemoryRecallResult:
    memory_needed: bool
    need_reason: str
    matched_rules: tuple[str, ...]
    query_mode: QueryMode
    retrieval_query: str | None
    candidates: tuple[RerankedMemoryCandidate, ...]
    recalled_memories: tuple[RerankedMemoryCandidate, ...]


class MemoryRecallPort(Protocol):
    def recall(
        self,
        current_user_message: str,
        recent_messages: Sequence[Mapping[str, str]],
    ) -> MemoryRecallResult: ...


class MemoryRecallService:
    """Run the frozen v0.1 need → context → semantic Top-10 → rerank → Top-3 flow."""

    def __init__(
        self,
        retriever: MemorySearch,
        reranker: MemoryReranker | None = None,
        *,
        need_policy: MemoryNeedPolicy | None = None,
        context_policy: MemoryContextPolicy | None = None,
    ) -> None:
        self._retriever = retriever
        self._reranker = reranker or MemoryReranker()
        self._need_policy = need_policy or MemoryNeedPolicy()
        self._context_policy = context_policy or MemoryContextPolicy()

    def recall(
        self,
        current_user_message: str,
        recent_messages: Sequence[Mapping[str, str]],
    ) -> MemoryRecallResult:
        need = self._need_policy.evaluate(current_user_message)
        if not need.needed:
            return MemoryRecallResult(
                memory_needed=False,
                need_reason=need.reason,
                matched_rules=need.matched_rules,
                query_mode="current_only",
                retrieval_query=None,
                candidates=(),
                recalled_memories=(),
            )

        context = self._context_policy.evaluate(current_user_message)
        messages = [dict(message) for message in recent_messages]
        messages.append({"role": "user", "content": current_user_message})
        query = build_query(messages, context.mode, RECENT_CONTEXT_MESSAGES)
        semantic_candidates = self._retriever.retrieve(query, top_n=SEMANTIC_TOP_N)
        ranked = self._reranker.rerank(query, semantic_candidates, top_n=SEMANTIC_TOP_N)
        return MemoryRecallResult(
            memory_needed=True,
            need_reason=need.reason,
            matched_rules=need.matched_rules,
            query_mode=context.mode,
            retrieval_query=query,
            candidates=ranked,
            recalled_memories=ranked[:INJECTION_TOP_K],
        )
