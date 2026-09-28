"""Minimal multi-turn terminal chat for SI-001."""

from pathlib import Path

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.conversation import Conversation
from evolving_companion.llm import LLMClient
from evolving_companion.storage import SQLiteStore


def main() -> None:
    """Read messages, print replies, and stop on ``/exit``."""
    try:
        seed_path = (
            Path(__file__).resolve().parents[2] / "data" / "characters" / "si_001.yaml"
        )
        seed_data = load_character_seed_data(seed_path)
        character_context = CharacterProjector().project(seed_data)
        conversation = Conversation(LLMClient(), character_context, SQLiteStore())
    except ValueError as error:
        print(error)
        return
    except OSError as error:
        print(f"无法读取 Character Seed Data：{error}")
        return

    print("和玲聊天。输入 /exit 退出。")
    while True:
        try:
            user_message = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if user_message == "/exit":
            break
        if not user_message:
            continue

        try:
            print(f"玲：{conversation.send(user_message)}")
        except Exception:  # Do not expose provider details or credentials in the CLI.
            print("请求失败，请检查网络与 DeepSeek API 配置后重试。")


if __name__ == "__main__":
    main()
