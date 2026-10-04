"""Thin adapter for the DeepSeek OpenAI-compatible chat completions API."""

import os
from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

from openai import OpenAI, DefaultHttpxClient

Message = Mapping[str, str]
MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com"


@dataclass(frozen=True)
class CompletionUsage:
    prompt_tokens: int
    completion_tokens: int


def llm_settings(values: Mapping[str, str]) -> tuple[str, str]:
    endpoint = values.get("SI_LLM_API_URL", BASE_URL)
    url = urlsplit(endpoint)
    if (
        url.scheme not in {"https", "http"}
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("Invalid LLM API URL")
    _ = url.port
    model = values.get("SI_LLM_MODEL", MODEL)
    if not model.strip():
        raise ValueError("LLM model is required")
    return endpoint, model


class LLMClient:
    """Translate standard chat messages into a DeepSeek text completion."""

    def __init__(
        self,
        *,
        max_retries: int = 2,
        environment: Mapping[str, str] | None = None,
        timeout: float | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        values = os.environ if environment is None else environment
        resolved_api_key = values.get("DEEPSEEK_API_KEY")
        if not resolved_api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY is not configured. Set it in the environment or "
                "create .env.local from .env.example."
            )
        endpoint, self.model = llm_settings(values)
        options = {} if timeout is None else {"timeout": timeout}
        self.max_output_tokens = max_output_tokens
        self.last_usage: CompletionUsage | None = None
        self._client = OpenAI(
            api_key=resolved_api_key,
            base_url=endpoint,
            max_retries=max_retries,
            http_client=DefaultHttpxClient(follow_redirects=False),
            **options,
        )

    def complete(self, messages: Sequence[Message]) -> str:
        self.last_usage = None
        options = (
            {}
            if self.max_output_tokens is None
            else {"max_tokens": self.max_output_tokens}
        )
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[dict(message) for message in messages],
            **options,
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.last_usage = CompletionUsage(
                usage.prompt_tokens, usage.completion_tokens
            )
        content = response.choices[0].message.content
        if content is None:
            raise RuntimeError("DeepSeek 返回了空回复。")
        return content

    def close(self) -> None:
        self._client.close()
