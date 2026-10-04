"""One-call, high-precision extraction and persistence gate for memory candidates."""

import json
from collections.abc import Mapping, Sequence
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evolving_companion.storage import MemoryRecord, SQLiteStore
from evolving_companion.affective import SignificantAffectiveEvent

MemoryType = Literal["episodic", "semantic", "self", "relationship"]
MemorySource = Literal["explicit", "observed", "inferred"]
Salience = Literal["low", "medium", "high"]
Decision = Literal["save", "reject", "uncertain"]


class ArchiveChunkMessage(BaseModel):
    """The archive message fields made available to one extraction call."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    role: Literal["user", "assistant"]
    content: str

    @field_validator("id")
    @classmethod
    def id_must_be_uuid(cls, value: str) -> str:
        try:
            UUID(value)
        except ValueError as error:
            raise ValueError("message id must be a UUID string") from error
        return value

    @field_validator("content")
    @classmethod
    def content_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message content must not be empty")
        return value


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content: str
    memory_type: MemoryType
    source: MemorySource
    salience: Salience
    decision: Decision
    evidence_refs: list[str]
    reason: str

    @field_validator("content")
    @classmethod
    def content_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be empty")
        return value


class MemoryExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidates: list[MemoryCandidate] = Field(max_length=5)


class TextCompletionClient(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> str: ...


MEMORY_EXTRACTION_PROMPT = """你是高精度的 Memory Extraction 与 Gate。只从给出的对话消息中提取，宁可漏掉普通信息，也不要把临时状态、猜测或弱推断写成长久事实。

只根据提供的对话内容判断，不得补充对话中未出现的信息，也不把模型常识当作 Character Experience。Developer 或 System 后台信息不是 Character 的 Lived Memory。不要为了让人物显得丰满而生成不存在的过去。

assistant 消息不能自动变成用户事实。不要把 assistant 生成的一般知识、建议或解释，抽取成用户的知识、观点、研究、计划或偏好；只有 user 消息明确提供的个人信息才支持这类 user memory。assistant 消息只能在确实发生重要关系事件、边界协商或对 Character 有持续意义的真实互动时，谨慎支持 relationship / self memory，也可用于澄清 user 已提供的信息。普通回答、附和或“好的”不构成 self memory。

分类定义：
- episodic：具体发生过的一件事情。
- semantic：相对稳定的事实或当前认识。
- self：与玲自身真实经历、身份或变化直接相关的长期记忆；开发后台发生的事情不能仅因“关于玲”就归为 self。
- relationship：玲与某个对象之间具有长期关系意义的事件或认识。
- explicit：用户或角色明确说出的信息。
- observed：对话中实际发生并可直接观察的事实或行为，只写“发生了什么”。
- inferred：根据一个或多个线索推断出的认识，清楚保留其推断性质；不要把推断写成观察事实。

Gate 规则：
- save：有充分理由进入长期 Memory。
- reject：不值得长期保存。
- uncertain：可能重要，但证据不足或可能只是短期变化。
- 明确且稳定的个人事实、长期计划或目标、明确偏好或厌恶、重要关系事件、对 Character 有意义的真实经历、明确更新或撤回旧信息，可以考虑 save。
- 寒暄、临时情绪或短期状态、普通一次性日常细节通常 reject。
- 如果用户明确取消、替代或更新长期计划、目标、偏好或持续选择，即使说“现在”，也可将该更新作为长期 Memory 考虑 save；例如“我现在不打算买 Mac 了”“我现在主要转做 Neural Operator 了”“我现在已经不喝咖啡了”。不要仅凭“现在”判断长期性；“今天/这两天/此刻不想……”或“我现在有点累”等仍是临时状态，通常 reject 或 uncertain。根据表达是否明确更新持续状态及其语境判断，不按单个关键词机械判断。
- 证据不足的推断通常 reject 或 uncertain。
- 每个候选都只能引用输入中真实存在的 message id；不得生成、猜测或修复 id。save 必须至少引用一条证据。
- 最多返回 5 条。没有值得提取的内容时返回空 candidates。

判定示例：
1. user: 我现在困死了。→ reject；短期状态。
2. user: 我生日是 10 月 7 日。→ save；semantic / explicit / high。
3. user: 最近好像没以前那么喜欢这个游戏了。→ uncertain；可能是偏好变化，但当前不足以判断是否稳定。

只输出一个符合要求结构的 JSON 对象，不要 Markdown 代码围栏或额外说明。对象格式：
{"candidates":[{"content":"...","memory_type":"semantic","source":"explicit","salience":"medium","decision":"save","evidence_refs":["输入中的message id"],"reason":"简短判定依据"}]}
"""


def _validate_chunk(
    messages: Sequence[ArchiveChunkMessage | Mapping[str, str]],
) -> tuple[ArchiveChunkMessage, ...]:
    return tuple(ArchiveChunkMessage.model_validate(message) for message in messages)


class MemoryExtractor:
    """Ask the current text completion client once and validate its JSON result."""

    def __init__(self, llm_client: TextCompletionClient) -> None:
        self._llm_client = llm_client

    def extract_memories(
        self, messages: Sequence[ArchiveChunkMessage | Mapping[str, str]]
    ) -> MemoryExtractionResult:
        return self._extract(messages)

    def extract_with_affect(
        self,
        messages: Sequence[ArchiveChunkMessage | Mapping[str, str]],
        hint: SignificantAffectiveEvent,
    ) -> MemoryExtractionResult:
        return self._extract(messages, hint)

    def _extract(
        self,
        messages: Sequence[ArchiveChunkMessage | Mapping[str, str]],
        hint: SignificantAffectiveEvent | None = None,
    ) -> MemoryExtractionResult:
        chunk = _validate_chunk(messages)
        input_content = json.dumps(
            [message.model_dump() for message in chunk], ensure_ascii=False
        )
        request = [
            {"role": "system", "content": MEMORY_EXTRACTION_PROMPT},
            {"role": "user", "content": input_content},
        ]
        if hint is not None and str(hint.source_event_id) in {m.id for m in chunk}:
            request.insert(
                1,
                {
                    "role": "system",
                    "content": "本轮有通过重要性 gate 的情绪线索："
                    + ", ".join(hint.emotions)
                    + "。这只是关注原始证据的提示，不是新证据或新的 memory source。"
                    "只有下方 Archive 内容独立支持且确实值得长期记住时才保存；"
                    "不要保存内部心情/关系数值，不得仅凭此提示制造亲历记忆。",
                },
            )
        response = self._llm_client.complete(request)
        return MemoryExtractionResult.model_validate_json(response)


def persist_saved_candidates(
    result: MemoryExtractionResult,
    messages: Sequence[ArchiveChunkMessage | Mapping[str, str]],
    store: SQLiteStore,
) -> tuple[MemoryRecord, ...]:
    """Persist only valid save candidates, attaching their original evidence."""
    chunk = _validate_chunk(messages)
    message_ids = {message.id for message in chunk}
    saved: list[MemoryRecord] = []

    for candidate in result.candidates:
        if candidate.decision != "save":
            continue
        if (
            not candidate.evidence_refs
            or len(set(candidate.evidence_refs)) != len(candidate.evidence_refs)
            or not set(candidate.evidence_refs) <= message_ids
        ):
            continue
        if store.has_active_memory_content(candidate.content):
            continue
        try:
            saved.append(
                store.create_memory(
                    memory_type=candidate.memory_type,
                    content=candidate.content,
                    source=candidate.source,
                    salience=candidate.salience,
                    evidence_refs=candidate.evidence_refs,
                )
            )
        except ValueError:
            # A chunk reference that is not actually archived cannot be persisted.
            continue

    return tuple(saved)
