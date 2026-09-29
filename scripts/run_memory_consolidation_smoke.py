"""Smoke-test A3.5 supersession using a temporary SQLite database."""

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.conversation import Conversation, TextCompletionClient
from evolving_companion.llm import LLMClient
from evolving_companion.local_env import load_local_env
from evolving_companion.memory_consolidation import (
    ConsolidationDecision,
    ConsolidationJudgment,
    ConsolidationJudgePort,
    MemoryConsolidationJudge,
    MemoryConsolidationService,
)
from evolving_companion.memory_extraction import (
    ArchiveChunkMessage,
    Decision,
    MemoryCandidate,
    MemoryExtractionResult,
    MemoryExtractor,
)
from evolving_companion.memory_formation import (
    MemoryFormationResult,
    MemoryFormationService,
)
from evolving_companion.memory_recall import (
    MemoryRecallPort,
    MemoryRecallResult,
    MemoryRecallService,
)
from evolving_companion.memory_reranker import MemoryReranker
from evolving_companion.memory_retrieval import MemoryRetriever
from evolving_companion.prompting import Message
from evolving_companion.storage import MemoryRecord, SQLiteStore


class FakeLLMClient(TextCompletionClient):
    def complete(self, messages: list[Message]) -> str:
        return "（A3.5 smoke test 回复）"


class DeterministicExtractor:
    def extract_memories(
        self, messages: Sequence[ArchiveChunkMessage | Mapping[str, str]]
    ) -> MemoryExtractionResult:
        chunk = tuple(ArchiveChunkMessage.model_validate(item) for item in messages)
        text = chunk[0].content
        facts: tuple[str, Decision] | None = None
        if "Mac" in text and ("买" in text or "购买" in text):
            facts = (
                "用户现在不打算买 Mac 了"
                if "不打算" in text
                else "用户计划以后买 Mac 做剪辑",
                "save",
            )
        elif "策略游戏" in text:
            facts = ("用户喜欢策略游戏", "save")
        elif "剧情游戏" in text:
            facts = ("用户也喜欢剧情游戏", "save")
        elif "咖啡" in text:
            facts = (
                "用户今天不想喝咖啡" if "今天" in text else "用户喜欢咖啡",
                "save",
            )
        if facts is None:
            return MemoryExtractionResult(candidates=[])
        content, decision = facts
        return MemoryExtractionResult(
            candidates=[
                MemoryCandidate(
                    content=content,
                    memory_type="semantic",
                    source="explicit",
                    salience="medium",
                    decision=decision,
                    evidence_refs=[chunk[0].id],
                    reason="deterministic smoke input mapping",
                )
            ]
        )


class DeterministicJudge(ConsolidationJudgePort):
    def judge(
        self, new_memory: MemoryRecord, old_memory: MemoryRecord
    ) -> ConsolidationJudgment:
        explicit_cancel = (
            "Mac" in new_memory.content
            and "不打算买" in new_memory.content
            and "Mac" in old_memory.content
            and ("计划" in old_memory.content or "考虑" in old_memory.content)
        )
        decision: ConsolidationDecision = (
            "supersede_old" if explicit_cancel else "keep_both"
        )
        return ConsolidationJudgment(
            decision=decision,
            reason="明确取消旧计划" if explicit_cancel else "两条信息可以同时成立",
        )


class CapturingRecallService(MemoryRecallPort):
    def __init__(self, service: MemoryRecallService) -> None:
        self.service = service
        self.last_result: MemoryRecallResult | None = None

    def recall(
        self,
        current_user_message: str,
        recent_messages: Sequence[Mapping[str, str]],
    ) -> MemoryRecallResult:
        self.last_result = self.service.recall(current_user_message, recent_messages)
        return self.last_result


def _saved_memory_id(result: MemoryFormationResult | None) -> str | None:
    if result is None or not result.saved_memory_ids:
        print("No new memory persisted; consolidation skipped for this case.")
        return None
    return result.saved_memory_ids[0]


def _print_turn(
    store: SQLiteStore, conversation: Conversation, text: str
) -> MemoryFormationResult | None:
    response = conversation.send(text)
    with closing(sqlite3.connect(store.path)) as connection:
        archive_pair = connection.execute(
            "SELECT id, role FROM archive_messages ORDER BY rowid DESC LIMIT 2"
        ).fetchall()
    formation = conversation.last_memory_formation_result
    print(f"\nUser: {text}\nAssistant: {response}")
    print(f"Archive IDs (newest first): {archive_pair!r}")
    if formation is None or not formation.candidates:
        print("Extraction candidates: []")
    else:
        print("Extraction candidates:")
        for candidate in formation.candidates:
            print(
                f"  {candidate.decision} | {candidate.content} | "
                f"evidence={candidate.evidence_refs}"
            )
    if formation is not None:
        print(f"Saved new IDs: {formation.saved_memory_ids}")
        for result in formation.consolidation_results:
            print(
                f"Consolidation: new={result.new_memory_id} "
                f"candidates={result.candidate_old_ids} "
                f"decisions={result.decisions} superseded={result.superseded_ids} "
                f"uncertain={result.uncertain_ids} error={result.error}"
            )
    print("Memory statuses:")
    with closing(sqlite3.connect(store.path)) as connection:
        rows = connection.execute(
            "SELECT id, status, content FROM memories ORDER BY created_at, id"
        ).fetchall()
    for memory_id, status, content in rows:
        print(f"  {status} | {memory_id} | {content}")
    return formation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real-llm",
        action="store_true",
        help="Use DeepSeek for extraction, replies, and consolidation judgments.",
    )
    args = parser.parse_args()
    if args.real_llm and not load_local_env():
        print(
            "DEEPSEEK_API_KEY is not configured. Set it in the environment or "
            "create .env.local from .env.example."
        )
        return 1
    seed_path = (
        Path(__file__).resolve().parents[1] / "data" / "characters" / "si_001.yaml"
    )
    character_context = CharacterProjector().project(
        load_character_seed_data(seed_path)
    )

    with TemporaryDirectory(prefix="si001-memory-consolidation-") as temp_dir:
        database_path = Path(temp_dir) / "smoke.db"
        print(f"Temporary database: {database_path} (removed on exit)")
        store = SQLiteStore(database_path)
        retriever = MemoryRetriever(store)
        llm_client: TextCompletionClient = (
            LLMClient() if args.real_llm else FakeLLMClient()
        )
        extractor = (
            MemoryExtractor(llm_client) if args.real_llm else DeterministicExtractor()
        )
        judge: ConsolidationJudgePort = (
            MemoryConsolidationJudge(llm_client)
            if args.real_llm
            else DeterministicJudge()
        )
        if not args.real_llm:
            print("Using fake reply client and deterministic extraction/judge.")
        recall = CapturingRecallService(
            MemoryRecallService(retriever, MemoryReranker())
        )
        consolidation = MemoryConsolidationService(store, retriever, judge)
        formation = MemoryFormationService(extractor, store, consolidation)
        conversation = Conversation(
            llm_client,
            character_context,
            store,
            memory_recall_service=recall,
            memory_formation_service=formation,
        )

        _print_turn(store, conversation, "我以后想买一台 Mac 做剪辑。")
        cancellation_result = _print_turn(
            store, conversation, "我现在不打算买 Mac 了。"
        )
        cancellation_id = _saved_memory_id(cancellation_result)
        plan_memory_ids = (
            {item.id for item in store.get_superseded_memories(cancellation_id)}
            if cancellation_id is not None
            else set()
        )
        if not args.real_llm and cancellation_id is not None:
            assert plan_memory_ids
        _print_turn(store, conversation, "那个计划现在还有效吗？")
        if not args.real_llm:
            assert recall.last_result is not None and recall.last_result.memory_needed
        recalled_ids = (
            {item.memory_id for item in recall.last_result.recalled_memories}
            if recall.last_result is not None
            else set()
        )
        print(f"Current-query recalled IDs: {sorted(recalled_ids)}")
        print(f"Superseded old-plan IDs: {sorted(plan_memory_ids)}")
        if cancellation_id is not None:
            if not args.real_llm:
                assert not recalled_ids.intersection(plan_memory_ids)
            cancellation_memory = store.get_memory(cancellation_id)
            if not args.real_llm:
                assert cancellation_memory is not None
                assert cancellation_memory.status == "active"

        _print_turn(store, conversation, "我喜欢策略游戏。")
        strategy_result = conversation.last_memory_formation_result
        strategy_id = _saved_memory_id(strategy_result)
        _print_turn(store, conversation, "我也喜欢剧情游戏。")
        story_result = conversation.last_memory_formation_result
        story_id = _saved_memory_id(story_result)
        if strategy_id is not None and story_id is not None:
            strategy_memory = store.get_memory(strategy_id)
            story_memory = store.get_memory(story_id)
            if not args.real_llm:
                assert (
                    strategy_memory is not None and strategy_memory.status == "active"
                )
                assert story_memory is not None and story_memory.status == "active"

        _print_turn(store, conversation, "我喜欢咖啡。")
        coffee_result = conversation.last_memory_formation_result
        coffee_id = _saved_memory_id(coffee_result)
        _print_turn(store, conversation, "我今天不想喝咖啡。")
        temporary_result = conversation.last_memory_formation_result
        temporary_id = _saved_memory_id(temporary_result)
        if coffee_id is not None and temporary_id is not None:
            coffee_memory = store.get_memory(coffee_id)
            temporary_memory = store.get_memory(temporary_id)
            if not args.real_llm:
                assert coffee_memory is not None and coffee_memory.status == "active"
                assert (
                    temporary_memory is not None and temporary_memory.status == "active"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
