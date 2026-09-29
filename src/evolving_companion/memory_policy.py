"""Small deterministic policies for deciding whether and how to recall memory."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

QueryMode = Literal["current_only", "recent_context"]

_RESET_CUES = (
    "换个话题",
    "不讨论我的",
    "不是，只",
    "不是，只查",
    "只查",
    "今天只想知道",
    "只想知道",
    "通用做法",
    "通用知识题",
    "学术史",
)
_PERSONAL_CUES = (
    "我的",
    "我平时",
    "我平常",
    "我以前",
    "我之前",
    "我一直",
    "按我",
    "适合我",
    "我会喜欢",
    "我喜欢",
    "我研究",
    "我在意",
    "还在意什么",
    "你还记得",
    "你之前为什么",
    "我们之前",
    "我们那个",
    "那个计划",
    "当时",
    "来着",
    "我为什么",
    "我最近为什么",
    "我脑子都转不动",
    "不会喜欢",
)
_UNRESOLVED_CUES = ("还没说是什么", "先不说", "不知道指哪个", "那这个呢", "那它呢")
_BACK_REFERENCE_CUES = (
    "这个",
    "那个",
    "它",
    "当时",
    "之前说的",
    "之前那个",
    "那个计划",
    "来着",
    "还合适吗",
    "为什么定下来",
)


@dataclass(frozen=True)
class MemoryNeedDecision:
    needed: bool
    reason: str
    matched_rules: tuple[str, ...]


@dataclass(frozen=True)
class MemoryContextDecision:
    mode: QueryMode
    reason: str
    matched_rules: tuple[str, ...]


def _normalized_text(message: str) -> str:
    if not isinstance(message, str) or not message.strip():
        raise ValueError("current message must be nonempty")
    return " ".join(message.split())


class MemoryNeedPolicy:
    """Prefer no recall unless the current message signals personal memory need."""

    def evaluate(self, current_user_message: str) -> MemoryNeedDecision:
        text = _normalized_text(current_user_message)
        if any(cue in text for cue in _UNRESOLVED_CUES):
            return MemoryNeedDecision(
                False, "当前指代没有足够信息解析", ("unresolved_reference",)
            )
        resets = tuple(f"topic_reset:{cue}" for cue in _RESET_CUES if cue in text)
        if resets:
            return MemoryNeedDecision(False, "当前消息明确排除个人记忆", resets)
        personal = tuple(f"personal:{cue}" for cue in _PERSONAL_CUES if cue in text)
        if personal:
            return MemoryNeedDecision(True, "命中个人事实、偏好或经历线索", personal)
        return MemoryNeedDecision(
            False, "未发现明确的个人记忆需求", ("no_personal_memory_cue",)
        )


class MemoryContextPolicy:
    """Include recent messages only when the current turn clearly points backward."""

    def evaluate(self, current_user_message: str) -> MemoryContextDecision:
        text = _normalized_text(current_user_message)
        if any(cue in text for cue in _UNRESOLVED_CUES + _RESET_CUES):
            return MemoryContextDecision(
                "current_only",
                "当前消息不应依赖或拼接旧上下文",
                ("unresolved_or_topic_reset",),
            )
        references = tuple(
            f"back_reference:{cue}" for cue in _BACK_REFERENCE_CUES if cue in text
        )
        if references:
            return MemoryContextDecision(
                "recent_context", "当前消息包含历史回指", references
            )
        return MemoryContextDecision(
            "current_only",
            "消息自包含或没有明确历史回指",
            ("self_contained_or_no_back_reference",),
        )


def build_query(
    messages: Sequence[Mapping[str, str]],
    mode: QueryMode,
    context_messages: int = 5,
) -> str:
    """Build a raw current or role-labelled recent-history retrieval query."""
    if (
        mode not in ("current_only", "recent_context")
        or type(context_messages) is not int
    ):
        raise ValueError("invalid query mode or context window")
    if not 3 <= context_messages <= 6:
        raise ValueError("context_messages must be between 3 and 6")
    if not messages:
        raise ValueError("messages must be a nonempty sequence")
    for message in messages:
        if (
            message.get("role") not in ("user", "assistant")
            or not isinstance(message.get("content"), str)
            or not message["content"].strip()
        ):
            raise ValueError(
                "messages require user/assistant role and nonempty content"
            )
    if messages[-1]["role"] != "user":
        raise ValueError("messages must end with the current user message")
    if mode == "current_only":
        return messages[-1]["content"]
    return "\n".join(
        f"{message['role']}: {message['content']}"
        for message in messages[-context_messages:]
    )
