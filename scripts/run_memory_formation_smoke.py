"""Exercise A3.4 formation and next-turn recall using a temporary SQLite DB."""

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
from evolving_companion.memory_extraction import (
    ArchiveChunkMessage,
    Decision,
    MemoryCandidate,
    MemoryExtractionResult,
    MemoryExtractor,
)
from evolving_companion.memory_formation import MemoryFormationService
from evolving_companion.memory_recall import (
    MemoryRecallPort,
    MemoryRecallResult,
    MemoryRecallService,
)
from evolving_companion.memory_reranker import MemoryReranker
from evolving_companion.memory_retrieval import MemoryRetriever
from evolving_companion.prompting import Message
from evolving_companion.storage import SQLiteStore


class FakeLLMClient(TextCompletionClient):
    def complete(self, messages: list[Message]) -> str:
        return "（A3.4 smoke test 回复）"


class DeterministicExtractor:
    """Produce illustrative deterministic decisions without calling a model."""

    def extract_memories(
        self, messages: Sequence[ArchiveChunkMessage | Mapping[str, str]]
    ) -> MemoryExtractionResult:
        chunk = tuple(ArchiveChunkMessage.model_validate(item) for item in messages)
        user_message = chunk[0].content
        decision: Decision
        if "生日" in user_message:
            content = "用户生日是 10 月 7 日。"
            decision = "save"
        elif "Mac" in user_message:
            content = "用户计划未来购买一台 Mac 用于视频剪辑。"
            decision = "save"
        elif "PINN" in user_message:
            content = "用户询问 PINN 的提出历史。"
            decision = "reject"
        else:
            content = "用户进行了一次日常问候。"
            decision = "reject"
        return MemoryExtractionResult(
            candidates=[
                MemoryCandidate(
                    content=content,
                    memory_type="semantic",
                    source="explicit",
                    salience="high",
                    decision=decision,
                    evidence_refs=[chunk[0].id],
                    reason="smoke test deterministic case",
                )
            ]
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


def _print_turn(
    store: SQLiteStore, conversation: Conversation, user_message: str
) -> None:
    response = conversation.send(user_message)
    with closing(sqlite3.connect(store.path)) as connection:
        archive_ids = connection.execute(
            "SELECT id, role FROM archive_messages ORDER BY rowid DESC LIMIT 2"
        ).fetchall()
    result = conversation.last_memory_formation_result
    print(f"\nUser: {user_message}\nAssistant: {response}")
    print(f"Archive IDs (newest first): {archive_ids!r}")
    print("Candidates:")
    if result is None or not result.candidates:
        print("  []")
    else:
        for candidate in result.candidates:
            print(
                f"  {candidate.decision} | {candidate.memory_type} | "
                f"{candidate.content} | evidence={candidate.evidence_refs} | "
                f"reason={candidate.reason}"
            )
    if result is not None:
        print(
            "Decision counts: "
            f"save={sum(item.decision == 'save' for item in result.candidates)}, "
            f"reject={result.rejected_count}, uncertain={result.uncertain_count}"
        )
        print(f"Saved memory IDs: {result.saved_memory_ids}")
    if result is not None and result.error:
        print(f"Formation error: {result.error}")
    print("Saved memories:")
    for memory in store.list_active_memories():
        print(f"  {memory.id} | {memory.content}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real-llm",
        action="store_true",
        help="Use DeepSeek for both replies and extraction (requires DEEPSEEK_API_KEY).",
    )
    args = parser.parse_args()
    seed_path = (
        Path(__file__).resolve().parents[1] / "data" / "characters" / "si_001.yaml"
    )
    character_context = CharacterProjector().project(
        load_character_seed_data(seed_path)
    )

    with TemporaryDirectory(prefix="si001-memory-formation-") as temporary_directory:
        database_path = Path(temporary_directory) / "smoke.db"
        print(f"Temporary database: {database_path} (removed when the script exits)")
        store = SQLiteStore(database_path)
        recall = CapturingRecallService(
            MemoryRecallService(MemoryRetriever(store), MemoryReranker())
        )
        if args.real_llm:
            llm_client = LLMClient()
            extractor = MemoryExtractor(llm_client)
        else:
            llm_client = FakeLLMClient()
            extractor = DeterministicExtractor()
            print(
                "Using fake main LLM and deterministic extractor; decisions are illustrative."
            )
        conversation = Conversation(
            llm_client,
            character_context,
            store,
            memory_recall_service=recall,
            memory_formation_service=MemoryFormationService(extractor, store),
        )

        print(f"Memories before birthday turn: {len(store.list_active_memories())}")
        _print_turn(store, conversation, "我的生日是 10 月 7 日。")
        _print_turn(store, conversation, "我的生日是哪天？")
        if recall.last_result is not None:
            print("Next-turn recalled memories:")
            for memory in recall.last_result.recalled_memories:
                print(f"  {memory.memory_id} | {memory.content}")
        _print_turn(store, conversation, "PINN 最早是哪篇论文提出的？")
        _print_turn(store, conversation, "早上好")
        _print_turn(store, conversation, "我以后想买一台 Mac 做剪辑。")


if __name__ == "__main__":
    main()
