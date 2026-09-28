"""Minimal multi-turn terminal chat for SI-001."""

from evolving_companion.conversation import Conversation
from evolving_companion.llm import LLMClient


def main() -> None:
    """Read messages, print replies, and stop on ``/exit``."""
    try:
        conversation = Conversation(LLMClient())
    except ValueError as error:
        print(error)
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
