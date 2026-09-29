"""Manually smoke-test production memory recall and prompt injection on a temp DB."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.conversation import Conversation, TextCompletionClient
from evolving_companion.llm import LLMClient
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
        return "（smoke test fake response）"


class CapturingClient(TextCompletionClient):
    def __init__(self, client: TextCompletionClient) -> None:
        self.client = client
        self.requests: list[list[Message]] = []

    def complete(self, messages: list[Message]) -> str:
        self.requests.append(messages)
        return self.client.complete(messages)


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


def print_case(name, recall, response, prompt_messages):
    result = recall.last_result
    assert result is not None
    print(f"\n=== {name} ===")
    print(f"Memory need: {result.memory_needed} | {result.need_reason}")
    print(f"Matched rules: {result.matched_rules}")
    print(f"Query mode: {result.query_mode}")
    print(f"Retrieval query: {result.retrieval_query!r}")
    print("Recalled memories:")
    if result.recalled_memories:
        for memory in result.recalled_memories:
            print(f"  {memory.memory_id} | {memory.content}")
    else:
        print("  []")
    prompt = prompt_messages[0]["content"]
    marker = "【可参考的长期记忆候选】"
    section = prompt[prompt.find(marker) :] if marker in prompt else "(none)"
    print(f"Prompt memory section:\n{section}")
    print(f"Assistant: {response}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real-llm",
        action="store_true",
        help="Use DeepSeek for replies (requires DEEPSEEK_API_KEY); otherwise use a fake client.",
    )
    args = parser.parse_args()
    seed_path = (
        Path(__file__).resolve().parents[1] / "data" / "characters" / "si_001.yaml"
    )
    character_context = CharacterProjector().project(
        load_character_seed_data(seed_path)
    )

    with TemporaryDirectory(prefix="si001-memory-injection-") as temporary_directory:
        database_path = Path(temporary_directory) / "smoke.db"
        print(f"Temporary database: {database_path} (removed when the script exits)")
        store = SQLiteStore(database_path)
        store.create_memory("semantic", "用户生日是 10 月 7 日。", "explicit", "high")
        store.create_memory("semantic", "用户正在研究 PINN。", "explicit", "medium")
        store.create_memory(
            "semantic",
            "用户计划未来购买一台 Mac，用于视频剪辑和写代码。",
            "explicit",
            "high",
        )
        service = MemoryRecallService(MemoryRetriever(store), MemoryReranker())
        base_client: TextCompletionClient = (
            LLMClient() if args.real_llm else FakeLLMClient()
        )

        def new_conversation():
            client = CapturingClient(base_client)
            recall = CapturingRecallService(service)
            conversation = Conversation(
                client,
                character_context,
                store,
                memory_recall_service=recall,
            )
            return conversation, recall, client

        cases = (
            ("A: personal birthday", "我的生日是哪天？"),
            ("B: generic PINN history", "PINN 最早是哪篇论文提出的？"),
        )
        for name, user_message in cases:
            conversation, recall, client = new_conversation()
            response = conversation.send(user_message)
            print_case(name, recall, response, client.requests[-1])

        conversation, recall, client = new_conversation()
        conversation.send("我最近在挑电脑。")
        conversation.send("剪视频，也会写代码。")
        response = conversation.send("那之前说的那个计划还合适吗？")
        print_case("C: context rescue", recall, response, client.requests[-1])

        conversation, recall, client = new_conversation()
        response = conversation.send("早上好。")
        print_case("D: greeting", recall, response, client.requests[-1])


if __name__ == "__main__":
    main()
