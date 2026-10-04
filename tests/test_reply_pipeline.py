from collections.abc import Mapping
from contextlib import ExitStack, closing
import json
import logging
from pathlib import Path
from random import Random
import sqlite3

import pytest
from pydantic import ValidationError

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.conversation import Conversation
from evolving_companion.expression import BASE_REPLY_STYLE, ExpressionSelector
from evolving_companion.expression_habits import HABITS
from evolving_companion.memory_formation import MemoryFormationResult
from evolving_companion.prompting import Message, PromptBuilder
from evolving_companion.qq_adapter import QQPrivateChatAdapter
from evolving_companion.reply_pipeline import (
    NaturalReplyPipeline,
    Replyer,
    create_reply_pipeline,
)
from evolving_companion.reply_planning import ReplyGuidance, ReplyPlanner, ReplyTarget
from evolving_companion.storage import SQLiteStore
from evolving_companion.llm import CompletionUsage

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads(
    (ROOT / "tests/fixtures/natural_conversation_cases.json").read_text(
        encoding="utf-8"
    )
)


def character():
    return CharacterProjector().project(
        load_character_seed_data(ROOT / "data/characters/si_001.yaml")
    )


def guidance(case) -> ReplyGuidance:
    return ReplyGuidance.model_validate(
        {
            "focus": case["goal"],
            "reply_act": case["act"],
            "scene": case["scene"],
            "tone": "casual",
            "prefer": case["prefer"],
            "avoid": case["avoid"],
            "reply_reference": case["goal"],
        }
    )


class Client:
    def __init__(self, *responses: str | Exception) -> None:
        self.responses = iter(responses)
        self.requests: list[list[Message]] = []

    def complete(self, messages: list[Message]) -> str:
        self.requests.append(messages)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_planning_selection_and_replyer_are_real_stages(case) -> None:
    plan = guidance(case)
    planner = Client(plan.model_dump_json())
    replyer = Client("短回复。")
    pipeline = NaturalReplyPipeline(
        ReplyPlanner(planner), Replyer(replyer), ExpressionSelector(rng=Random(4))
    )
    context = PromptBuilder(character()).build(case.get("history", []), case["text"])
    original = [dict(m) for m in context]
    assert pipeline.reply(context, ReplyTarget(case["text"])) == "短回复。"
    assert context == original
    assert len(planner.requests) == len(replyer.requests) == 1
    payload = json.loads(planner.requests[0][-1]["content"])
    assert payload["target"]["text"] == case["text"]
    assert payload["history"] == case.get("history", [])
    diagnostic = pipeline.last_diagnostics
    assert diagnostic is not None and diagnostic.planner_error is None
    assert diagnostic.reply_act == case["act"]
    assert diagnostic.selected_expression_ids
    final = replyer.requests[0]
    assert final[-1] == {"role": "user", "content": case["text"]}
    assert final[0] == context[0]  # Character and other context remain intact.
    assert BASE_REPLY_STYLE in final[1]["content"]
    assert plan.reply_reference in final[1]["content"]
    for habit in HABITS:
        if habit.id in diagnostic.selected_expression_ids:
            assert habit.style in final[1]["content"]
    assert "不以续聊为目标" in final[1]["content"]
    assert diagnostic.planner_prompt_tokens is None  # Fake is not a token measurement.


def test_selector_is_weighted_contextual_avoids_and_can_select_zero_to_many() -> None:
    assert 15 <= len(HABITS) <= 30
    assert len({h.id for h in HABITS}) == len(HABITS)
    selector = ExpressionSelector(rng=Random(7))
    intent = guidance(CASES[6]).expression_intent()
    possibilities = {
        tuple(h.id for h in selector.select(intent, CASES[6]["text"]))
        for _ in range(60)
    }
    assert len(possibilities) > 2
    assert any(len(s) > 1 for s in possibilities)
    avoided = intent.model_copy(update={"avoid": ("curiosity",)})
    assert selector.select(avoided, CASES[6]["text"]) == ()
    assert ExpressionSelector(habits=()).select(intent, "anything") == ()
    # A keyword cannot force an incompatible act, and preferences change score.
    direct = guidance(CASES[1]).expression_intent()
    assert {h.id for h, _ in selector.candidates(direct, "项目奇怪离谱")} == {
        "own_state"
    }
    first = ExpressionSelector().candidates(direct, "")[0][1]
    second = ExpressionSelector().candidates(
        direct.model_copy(update={"prefer": ()}), ""
    )[0][1]
    assert first > second


def test_temporary_styles_optional_contextual_cooldown_and_reach_replyer() -> None:
    selector = ExpressionSelector(rng=Random(1))
    intent = guidance(CASES[2]).expression_intent()
    style = selector.temporary_style(intent)
    assert style is not None and style.id == "brief"
    assert selector.temporary_style(intent) is None
    assert selector.temporary_style(intent) is None
    context = PromptBuilder(character()).build([], "还在吗")
    messages = Replyer(Client()).build_messages(
        context, ReplyTarget("还在吗"), guidance(CASES[2]), intent, (), style
    )
    assert style.text in messages[1]["content"]
    assert "不改变人格" in messages[1]["content"]
    clarify = guidance(CASES[7]).expression_intent()
    assert selector.temporary_style(clarify) is None


@pytest.mark.parametrize(
    "bad", ["not JSON", '{"focus":"private"}', TimeoutError("secret-private-content")]
)
def test_planner_failure_is_explicit_bounded_and_does_not_leak(bad, caplog) -> None:
    planner, replyer = Client(bad), Client("仍可回应。")
    pipeline = NaturalReplyPipeline(ReplyPlanner(planner), Replyer(replyer))
    with caplog.at_level(logging.DEBUG):
        assert (
            pipeline.reply(
                PromptBuilder(character()).build([], "private-input"),
                ReplyTarget("private-input"),
            )
            == "仍可回应。"
        )
    assert len(planner.requests) == 1
    assert pipeline.last_diagnostics is not None
    assert pipeline.last_diagnostics.planner_error
    assert pipeline.last_diagnostics.selected_expression_ids == ()
    assert "private-input" not in caplog.text
    assert "secret-private-content" not in caplog.text


def test_untrusted_guidance_rejected_not_cast_into_protocol() -> None:
    valid = guidance(CASES[0]).model_dump()
    for field, invalid in (
        ("reply_act", "steal-secret"),
        ("tone", "private user text"),
        ("focus", "x" * 241),
    ):
        with pytest.raises(ValidationError):
            ReplyGuidance.model_validate(valid | {field: invalid})
    with pytest.raises(ValidationError):
        ReplyGuidance.model_validate(valid | {"prefer": ["brief"], "avoid": ["brief"]})


def test_planner_budget_and_target_are_separate_from_old_history() -> None:
    history = [{"role": "user", "content": "old" * 2000}] * 30
    context = PromptBuilder(character()).build(history, "current")
    planner = ReplyPlanner(Client())
    messages = planner.build_messages(context, ReplyTarget("current"))
    payload = json.loads(messages[-1]["content"])
    assert len(payload["history"]) == 12
    assert len(payload["history"][0]["content"]) < 850
    assert payload["target"]["text"] == "current"
    assert "最近用户表达不想频繁被问时" in messages[1]["content"]
    assert "必要 clarification 仍保留" in messages[1]["content"]
    assert "未记录不等于在发呆" in messages[1]["content"]
    with pytest.raises(ValueError):
        planner.build_messages(
            [{"role": "system", "content": "x" * 20001}], ReplyTarget("x")
        )


def test_reply_target_mismatch_rejected_and_non_user_trigger_supported() -> None:
    plan = guidance(CASES[0])
    replyer = Replyer(Client("一条表达。"))
    context = PromptBuilder(character()).build([], "旧消息")
    with pytest.raises(ValueError):
        replyer.build_messages(
            context, ReplyTarget("当前消息"), plan, plan.expression_intent(), (), None
        )
    pipeline = NaturalReplyPipeline(
        ReplyPlanner(Client(plan.model_dump_json())), replyer
    )
    assert (
        pipeline.reply(
            context[:1], ReplyTarget("受信调用者提供的上下文", "contextual_trigger")
        )
        == "一条表达。"
    )


@pytest.mark.parametrize("failure", [None, "replyer", "archive", "formation"])
def test_natural_pipeline_preserves_archive_completion_and_formation_boundary(
    tmp_path, monkeypatch, failure
) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    plan = guidance(CASES[2])
    planner = Client(plan.model_dump_json())
    replyer = Client(
        RuntimeError("private failure") if failure == "replyer" else "在的。"
    )
    formation_calls = []

    class Formation:
        def process_turn(
            self,
            user_archive_message: Mapping[str, str],
            assistant_archive_message: Mapping[str, str],
        ) -> MemoryFormationResult:
            formation_calls.append((user_archive_message, assistant_archive_message))
            assert conversation.history[-1]["content"] == "在的。"
            if failure == "formation":
                raise RuntimeError("private failure")
            return MemoryFormationResult()

    conversation = Conversation(
        replyer,
        character(),
        store,
        memory_formation_service=Formation(),
        reply_pipeline=NaturalReplyPipeline(ReplyPlanner(planner), Replyer(replyer)),
    )
    original_append = store.append_archive_message
    if failure == "archive":

        def append(conversation_id, role, content):
            if role == "assistant":
                raise RuntimeError("archive failed")
            return original_append(conversation_id, role, content)

        monkeypatch.setattr(store, "append_archive_message", append)
    if failure in {"replyer", "archive"}:
        with pytest.raises(RuntimeError):
            conversation.send("还在吗")
        assert conversation.history == ()
        assert formation_calls == []
    else:
        assert conversation.send("还在吗") == "在的。"
        assert len(conversation.history) == 2 and len(formation_calls) == 1
    with closing(sqlite3.connect(store.path)) as db:
        contents = db.execute(
            "SELECT content FROM archive_messages ORDER BY rowid"
        ).fetchall()
    assert contents == (
        [("还在吗",)]
        if failure in {"replyer", "archive"}
        else [("还在吗",), ("在的。",)]
    )


def test_qq_uses_real_pipeline_but_does_not_send_planning_to_transport(
    tmp_path,
) -> None:
    planner, replyer = Client(guidance(CASES[0]).model_dump_json()), Client("你好。")
    core = Conversation(
        replyer,
        character(),
        SQLiteStore(tmp_path / "qq.db"),
        reply_pipeline=NaturalReplyPipeline(ReplyPlanner(planner), Replyer(replyer)),
    )
    adapter = QQPrivateChatAdapter(core, allowed_user_ids={101}, bot_user_id=202)
    event = {
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "user_id": 101,
        "message_id": 1,
        "raw_message": "你好",
    }
    result = adapter.handle_event(event)
    assert result.response is not None and result.response.text == "你好。"
    assert adapter.handle_event(event).status == "duplicate"
    assert len(planner.requests) == len(replyer.requests) == 1


def test_factory_default_natural_legacy_and_resource_cleanup(monkeypatch) -> None:
    from evolving_companion import reply_pipeline

    created = []

    class PlannerClient(Client):
        def __init__(self, **kwargs):
            super().__init__()
            self.options = kwargs
            self.closed = False
            created.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(reply_pipeline, "LLMClient", PlannerClient)
    with ExitStack() as resources:
        assert create_reply_pipeline(resources, Client(), {}) is not None
        assert (
            create_reply_pipeline(resources, Client(), {"SI_REPLY_PIPELINE": "legacy"})
            is None
        )
        with pytest.raises(ValueError):
            create_reply_pipeline(resources, Client(), {"SI_REPLY_PIPELINE": "invalid"})
        assert len(created) == 1
        assert created[0].options["max_retries"] == 0
        assert created[0].options["max_output_tokens"] == 768
        assert created[0].options["timeout"] == 8.0
    assert created[0].closed


def test_usage_belongs_to_planning_not_final_reply() -> None:
    class MeteredClient(Client):
        last_usage: CompletionUsage | None = None

        def complete(self, messages: list[Message]) -> str:
            result = super().complete(messages)
            self.last_usage = CompletionUsage(len(self.requests) * 100, 10)
            return result

    client = MeteredClient(guidance(CASES[2]).model_dump_json(), "在的。")
    pipeline = NaturalReplyPipeline(ReplyPlanner(client), Replyer(client))
    pipeline.reply(
        PromptBuilder(character()).build([], "还在吗"), ReplyTarget("还在吗")
    )
    assert pipeline.last_diagnostics is not None
    assert pipeline.last_diagnostics.planner_prompt_tokens == 100


def test_memory_and_state_context_are_preserved_in_both_calls() -> None:
    class Memory:
        memory_type = "semantic"
        source = "explicit"
        content = "合成测试偏好：喜欢策略游戏。"

    from evolving_companion.character_state import CharacterState
    from datetime import datetime, timezone

    state = CharacterState.model_validate(
        dict(
            character_id=load_character_seed_data(
                ROOT / "data/characters/si_001.yaml"
            ).identity.internal_id,
            updated_at=datetime.now(timezone.utc),
            current_activity="合成测试活动",
        )
    )
    context = PromptBuilder(character()).build([], "测试", (Memory(),), state)
    planner, replyer = Client(guidance(CASES[1]).model_dump_json()), Client("测试回复")
    NaturalReplyPipeline(ReplyPlanner(planner), Replyer(replyer)).reply(
        context, ReplyTarget("测试")
    )
    for messages in (planner.requests[0], replyer.requests[0]):
        assert "合成测试活动" in messages[0]["content"]
        assert Memory.content in messages[0]["content"]
        assert "不是系统事实" in messages[0]["content"]
        assert "Character 不等于底层 LLM" in messages[0]["content"]


def test_user_archived_before_planning_and_failure_keeps_only_final_answer(
    tmp_path,
) -> None:
    store = SQLiteStore(tmp_path / "ordering.db")

    class PlannerClient:
        def complete(self, messages: list[Message]) -> str:
            with closing(sqlite3.connect(store.path)) as db:
                assert db.execute(
                    "SELECT role, content FROM archive_messages"
                ).fetchall() == [("user", "你好")]
            raise TimeoutError("private provider detail")

    replyer = Client("你好。")
    core = Conversation(
        replyer,
        character(),
        store,
        reply_pipeline=NaturalReplyPipeline(
            ReplyPlanner(PlannerClient()), Replyer(replyer)
        ),
    )
    assert core.send("你好") == "你好。"
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute(
            "SELECT role, content FROM archive_messages ORDER BY rowid"
        ).fetchall() == [("user", "你好"), ("assistant", "你好。")]


def test_no_output_blacklist_or_padding_for_any_generated_text() -> None:
    # Policy acts before generation; the final output is not secretly rewritten.
    for text in ("嗯。", "有多离谱？", "你呢？", "这里是一段较长的、必要的解释。" * 30):
        planner = Client(guidance(CASES[6]).model_dump_json())
        pipeline = NaturalReplyPipeline(ReplyPlanner(planner), Replyer(Client(text)))
        assert (
            pipeline.reply(
                PromptBuilder(character()).build([], "项目"), ReplyTarget("项目")
            )
            == text
        )
