"""Thin adapter for the DeepSeek OpenAI-compatible chat completions API."""

import os
from collections.abc import Mapping, Sequence

from openai import OpenAI

Message = Mapping[str, str]
MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com"


class LLMClient:
    """Translate standard chat messages into a DeepSeek text completion."""

    def __init__(self, *, max_retries: int = 2) -> None:
        resolved_api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not resolved_api_key:
            raise ValueError("请先设置环境变量 DEEPSEEK_API_KEY。")
        self._client = OpenAI(
            api_key=resolved_api_key,
            base_url=BASE_URL,
            max_retries=max_retries,
        )

    def complete(self, messages: Sequence[Message]) -> str:
        response = self._client.chat.completions.create(
            model=MODEL,
            messages=[dict(message) for message in messages],
        )
        content = response.choices[0].message.content
        if content is None:
            raise RuntimeError("DeepSeek 返回了空回复。")
        return content
