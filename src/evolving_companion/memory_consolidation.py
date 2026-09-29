"""Conservatively supersede explicitly outdated active memories."""

import json
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from evolving_companion.memory_extraction import TextCompletionClient
from evolving_companion.memory_retrieval import (
    MemoryRetrievalCandidate,
)
from evolving_companion.storage import MemoryRecord, SQLiteStore

ConsolidationDecision = Literal["keep_both", "supersede_old", "uncertain"]
CONSOLIDATION_CANDIDATE_LIMIT = 5

MEMORY_CONSOLIDATION_PROMPT = """你只负责判断一对已有记忆之间是否存在明确的当前状态替代关系。

仅当 new_memory 明确表示 old_memory 已不再是当前有效状态，才选择 supersede_old。要求存在清楚的更新、取消、否定或替代表达；不能仅凭主题相似、语义接近或常识推断冲突。

以下情况选择 keep_both：信息补充或更具体、并列偏好、不同粒度、不同对象、不同时间范围、可以同时成立，或仅为某一天/短期状态。证据不足但可能冲突时选择 uncertain。宁可保留两条，也不要误 supersede。

只能输出 decision 和简短 reason。不得改写、合并、总结记忆，不得生成第三条事实，不得改变类型、来源或重要性。只输出符合 JSON schema 的对象，不要 Markdown 或额外文字。
"""


class ConsolidationJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: ConsolidationDecision
    reason: str


class ConsolidationJudgePort(Protocol):
    def judge(
        self, new_memory: MemoryRecord, old_memory: MemoryRecord
    ) -> ConsolidationJudgment: ...


class MemorySearch(Protocol):
    def retrieve(
        self, query: str, top_n: int = 10
    ) -> list[MemoryRetrievalCandidate]: ...


@dataclass(frozen=True)
class MemoryConsolidationOutcome:
    old_memory_id: str
    decision: ConsolidationDecision
    reason: str


@dataclass(frozen=True)
class MemoryConsolidationResult:
    new_memory_id: str
    candidate_old_ids: tuple[str, ...] = ()
    decisions: tuple[MemoryConsolidationOutcome, ...] = ()
    superseded_ids: tuple[str, ...] = ()
    uncertain_ids: tuple[str, ...] = ()
    error: str | None = None


class MemoryConsolidationJudge:
    """Use the existing text-completion adapter for one pairwise judgment."""

    def __init__(self, llm_client: TextCompletionClient) -> None:
        self._llm_client = llm_client

    def judge(
        self, new_memory: MemoryRecord, old_memory: MemoryRecord
    ) -> ConsolidationJudgment:
        pair = {
            "new_memory": {"id": new_memory.id, "content": new_memory.content},
            "old_memory": {"id": old_memory.id, "content": old_memory.content},
        }
        response = self._llm_client.complete(
            [
                {"role": "system", "content": MEMORY_CONSOLIDATION_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(pair, ensure_ascii=False),
                },
            ]
        )
        return ConsolidationJudgment.model_validate_json(response)


class MemoryConsolidationService:
    """Retrieve a small candidate set, judge all pairs, then update atomically."""

    def __init__(
        self,
        store: SQLiteStore,
        retriever: MemorySearch,
        judge: ConsolidationJudgePort,
    ) -> None:
        self._store = store
        self._retriever = retriever
        self._judge = judge

    def process_new_memory(self, memory_id: str) -> MemoryConsolidationResult:
        candidate_old_ids: tuple[str, ...] = ()
        decisions: list[MemoryConsolidationOutcome] = []
        uncertain_ids: tuple[str, ...] = ()
        try:
            new_memory = self._store.get_memory(memory_id)
            if new_memory is None or new_memory.status != "active":
                return MemoryConsolidationResult(
                    new_memory_id=memory_id, error="NewMemoryNotActive"
                )

            # Fetch one extra result because the new memory itself is usually top-1.
            retrieved = self._retriever.retrieve(
                new_memory.content, top_n=CONSOLIDATION_CANDIDATE_LIMIT + 1
            )
            old_memories: list[MemoryRecord] = []
            seen_ids = {memory_id}
            for candidate in retrieved:
                if candidate.memory_id in seen_ids:
                    continue
                seen_ids.add(candidate.memory_id)
                old_memory = self._store.get_memory(candidate.memory_id)
                if old_memory is not None and old_memory.status == "active":
                    old_memories.append(old_memory)
                if len(old_memories) == CONSOLIDATION_CANDIDATE_LIMIT:
                    break

            candidate_old_ids = tuple(item.id for item in old_memories)
            for old_memory in old_memories:
                judgment = self._judge.judge(new_memory, old_memory)
                decisions.append(
                    MemoryConsolidationOutcome(
                        old_memory_id=old_memory.id,
                        decision=judgment.decision,
                        reason=judgment.reason,
                    )
                )
            decision_results = tuple(decisions)
            superseded_ids = tuple(
                item.old_memory_id
                for item in decision_results
                if item.decision == "supersede_old"
            )
            uncertain_ids = tuple(
                item.old_memory_id
                for item in decision_results
                if item.decision == "uncertain"
            )
            if superseded_ids:
                self._store.supersede_memories(superseded_ids, memory_id)
            return MemoryConsolidationResult(
                new_memory_id=memory_id,
                candidate_old_ids=candidate_old_ids,
                decisions=decision_results,
                superseded_ids=superseded_ids,
                uncertain_ids=uncertain_ids,
            )
        except Exception as error:
            # No old state changes occur before all judgments complete.
            return MemoryConsolidationResult(
                new_memory_id=memory_id,
                candidate_old_ids=candidate_old_ids,
                decisions=tuple(decisions),
                uncertain_ids=uncertain_ids,
                error=type(error).__name__,
            )
