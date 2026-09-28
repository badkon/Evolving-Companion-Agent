"""Coordinate one conversation turn while keeping history in memory."""

from collections.abc import Mapping
from typing import Protocol

from evolving_companion.character import CharacterProfile
from evolving_companion.prompting import Message, PromptBuilder


class TextCompletionClient(Protocol):
    """The conversation layer depends on messages-to-text behavior only."""

    def complete(self, messages: list[Message]) -> str: ...


class Conversation:
    """Build prompts, request replies, and append successful turns in order."""

    def __init__(
        self,
        llm_client: TextCompletionClient,
        profile: CharacterProfile | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._prompt_builder = PromptBuilder(profile or CharacterProfile())
        self._history: list[Message] = []

    @property
    def history(self) -> tuple[Mapping[str, str], ...]:
        """Return a read-only snapshot of the in-memory conversation history."""
        return tuple(dict(message) for message in self._history)

    def send(self, user_message: str) -> str:
        messages = self._prompt_builder.build(self._history, user_message)
        reply = self._llm_client.complete(messages)
        self._history.extend(
            (
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": reply},
            )
        )
        return reply
