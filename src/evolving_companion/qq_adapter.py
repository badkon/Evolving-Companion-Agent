"""Offline OneBot-style private-text boundary; no transport or persistence."""

from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID, NAMESPACE_URL, uuid5

from evolving_companion.media_input import MediaReference, VisionInputService
from evolving_companion.vision import MAX_IMAGES, VisualInput, VisualUnavailable


class ConversationPort(Protocol):
    def send(self, user_message: str) -> str: ...


@runtime_checkable
class TargetedConversationPort(Protocol):
    def send_for(self, user_message: str, target_id: UUID) -> str: ...


@runtime_checkable
class VisualConversationPort(Protocol):
    @property
    def supports_visual_input(self) -> bool: ...

    def send_visual_for(
        self, user_message: str, target_id: UUID, observations: tuple[VisualInput, ...]
    ) -> str: ...


def relation_target_id(sender_id: int | str) -> UUID:
    """Stable pseudonymous routing key, not a secret or proof of anonymity."""
    return uuid5(NAMESPACE_URL, "si:qq-private:" + _identifier(sender_id, user=True))


@dataclass(frozen=True)
class ExternalChatMessage:
    platform: Literal["qq"]
    conversation_type: Literal["private"]
    sender_id: str
    message_id: str
    text: str
    occurred_at: datetime | None
    images: tuple[MediaReference, ...] = ()
    image_count: int = 0


@dataclass(frozen=True)
class ExternalChatResponse:
    reply_to_message_id: str
    recipient_id: str
    text: str


@dataclass(frozen=True)
class AdapterHandlingResult:
    status: Literal["handled", "ignored", "duplicate", "unauthorized", "failed"]
    response: ExternalChatResponse | None = None
    reason: str | None = None


def _identifier(value: object, *, user: bool = False) -> str:
    if type(value) is int:
        number = value
    elif isinstance(value, str) and (
        value.isascii() and value.removeprefix("-").isdigit()
    ):
        number = int(value)
    else:
        raise ValueError("invalid identifier")
    if user and number <= 0:
        raise ValueError("invalid user identifier")
    return str(number)


def _parse_private_message(event: Mapping[str, object]) -> ExternalChatMessage:
    sender_id = _identifier(event.get("user_id"), user=True)
    message_id = _identifier(event.get("message_id"))
    images = []
    image_count = 0
    segments = event.get("message")
    if isinstance(segments, list):
        if len(segments) > 100:
            raise ValueError("Too many message segments")
        parts = []
        for segment in segments:
            if not isinstance(segment, Mapping) or not isinstance(
                segment.get("data"), Mapping
            ):
                raise ValueError("Invalid message segment")
            data = segment["data"]
            if segment.get("type") == "text" and isinstance(data.get("text"), str):
                parts.append(data["text"])
            elif segment.get("type") == "image":
                image_count += 1
                if len(images) < MAX_IMAGES:
                    url = data.get("url") or data.get("file")
                    images.append(
                        MediaReference(
                            url if isinstance(url, str) and len(url) <= 4096 else None
                        )
                    )
            else:
                raise ValueError("Unsupported message segment")
        text = "".join(parts)
    else:
        text = event.get("raw_message", event.get("text"))
    if not isinstance(text, str) or (not text.strip() and not images):
        raise ValueError("invalid text")
    if "[CQ:" in text:
        raise ValueError("unsupported message content")
    timestamp = event.get("time")
    occurred_at = None
    if timestamp is not None:
        if type(timestamp) is not int or timestamp < 0:
            raise ValueError("invalid event time")
        occurred_at = datetime.fromtimestamp(timestamp, timezone.utc)
    return ExternalChatMessage(
        "qq",
        "private",
        sender_id,
        message_id,
        text,
        occurred_at,
        tuple(images),
        image_count,
    )


class QQPrivateChatAdapter:
    """Handle serial private events using an injected synchronous Conversation."""

    def __init__(
        self,
        conversation: ConversationPort,
        *,
        allowed_user_ids: Iterable[int | str],
        bot_user_id: int | str,
        dedup_capacity: int = 1000,
        vision_input: VisionInputService | None = None,
    ) -> None:
        if type(dedup_capacity) is not int or dedup_capacity <= 0:
            raise ValueError("dedup_capacity must be a positive integer")
        self._conversation = conversation
        self._vision_input = vision_input or VisionInputService()
        self._allowed_user_ids = frozenset(
            _identifier(value, user=True) for value in allowed_user_ids
        )
        self._bot_user_id = _identifier(bot_user_id, user=True)
        self._dedup_capacity = dedup_capacity
        self._seen: OrderedDict[str, None] = OrderedDict()

    def handle_event(self, event: object) -> AdapterHandlingResult:
        if not isinstance(event, Mapping):
            return AdapterHandlingResult("ignored", reason="invalid_event")
        if event.get("post_type") != "message":
            return AdapterHandlingResult("ignored", reason="unsupported_event")
        if event.get("message_type") != "private":
            return AdapterHandlingResult("ignored", reason="private_only")
        # Explicit friend subtype excludes group temporary and unknown sessions.
        if event.get("sub_type") != "friend":
            return AdapterHandlingResult(
                "ignored", reason="unsupported_private_subtype"
            )
        try:
            message = _parse_private_message(event)
        except (ValueError, OverflowError, OSError):
            return AdapterHandlingResult("ignored", reason="invalid_private_message")
        if message.sender_id == self._bot_user_id:
            return AdapterHandlingResult("ignored", reason="self_message")
        if message.sender_id not in self._allowed_user_ids:
            return AdapterHandlingResult("unauthorized", reason="sender_not_allowed")
        if message.message_id in self._seen:
            return AdapterHandlingResult(
                "duplicate", reason="message_already_attempted"
            )
        # Remember attempts, not only successes: Core failure may follow user archival.
        self._seen[message.message_id] = None
        if len(self._seen) > self._dedup_capacity:
            self._seen.popitem(last=False)
        try:
            if message.images:
                text = (
                    message.text
                    + f"\n[用户发送图片，共{message.image_count}张；至多读取前{MAX_IMAGES}张]"
                )
                if isinstance(self._conversation, VisualConversationPort):
                    observations = (
                        self._vision_input.observe(message.images, message.text)
                        if self._conversation.supports_visual_input
                        else (VisualUnavailable("unsupported_mode"),)
                    )
                    reply = self._conversation.send_visual_for(
                        text, relation_target_id(message.sender_id), observations
                    )
                else:
                    text += "\n[当前未读取图片内容]"
                    reply = (
                        self._conversation.send_for(
                            text, relation_target_id(message.sender_id)
                        )
                        if isinstance(self._conversation, TargetedConversationPort)
                        else self._conversation.send(text)
                    )
            else:
                reply = (
                    self._conversation.send_for(
                        message.text, relation_target_id(message.sender_id)
                    )
                    if isinstance(self._conversation, TargetedConversationPort)
                    else self._conversation.send(message.text)
                )
        except Exception:
            # Never expose provider details or synthesize Character speech.
            return AdapterHandlingResult("failed", reason="conversation_failed")
        return AdapterHandlingResult(
            "handled",
            ExternalChatResponse(message.message_id, message.sender_id, reply),
        )
