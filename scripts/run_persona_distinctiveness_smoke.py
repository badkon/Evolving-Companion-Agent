"""Offline synthetic Persona wiring smoke; no API, database or real chat data."""

from dataclasses import asdict
import json
from pathlib import Path
from random import Random
from statistics import mean
from time import perf_counter

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_projection import (
    CharacterProjector,
    select_trait_candidates,
)
from evolving_companion.expression import ExpressionSelector
from evolving_companion.prompting import Message, PromptBuilder
from evolving_companion.reply_pipeline import NaturalReplyPipeline, Replyer
from evolving_companion.reply_planning import ReplyGuidance, ReplyPlanner, ReplyTarget


class SyntheticPlanner:
    """Only an explicit fixture policy, not a production or semantic planner."""

    def __init__(self, *, choose_none: bool = False) -> None:
        self.calls = 0
        self.guidance: ReplyGuidance | None = None
        self.choose_none = choose_none

    def complete(self, messages: list[Message]) -> str:
        self.calls += 1
        data = json.loads(messages[-1]["content"])
        chosen = [] if self.choose_none else data.get("persona_candidates", [])[:3]
        self.guidance = ReplyGuidance.model_validate(
            dict(
                focus="回应合成输入",
                reply_act="react",
                scene="ordinary",
                tone="casual",
                prefer=["direct"],
                avoid=["curiosity"],
                reply_reference="只使用选中设定和事实，允许简短结束；本结果是 fake guidance。",
                persona_relevance=dict(
                    active_traits=[t["id"] for t in chosen],
                    strength="high" if chosen else "none",
                    reason="离线 fake 取前几个候选，仅验证数据路径。",
                ),
            )
        )
        return self.guidance.model_dump_json()


class SyntheticReplyer:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[Message]) -> str:
        self.calls += 1
        assert messages[-1]["role"] == "user"
        return "[synthetic reply: Planner → selected traits → Replyer；非真实质量评估]"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    character = CharacterProjector().project(
        load_character_seed_data(root / "data/characters/si_001.yaml")
    )
    cases = json.loads(
        (root / "tests/fixtures/persona_activation_cases.json").read_text(
            encoding="utf-8"
        )
    )
    differences, selection_ms, active_sizes = [], [], []
    for case in cases:
        text = case["text"]
        history = [
            {"role": "user", "content": text} for text in case.get("history", [])
        ]
        planner, replyer = (
            SyntheticPlanner(choose_none=case.get("choose_none", False)),
            SyntheticReplyer(),
        )
        pipeline = NaturalReplyPipeline(
            ReplyPlanner(planner), Replyer(replyer), ExpressionSelector(rng=Random(1))
        )
        context = PromptBuilder(character).build(history, text, persona_managed=True)
        result = pipeline.reply(context, ReplyTarget(text), character=character)
        assert planner.calls == replyer.calls == 1
        assert pipeline.last_diagnostics is not None and planner.guidance is not None
        assert pipeline.last_diagnostics.planner_error is None
        # Same-code control omits persona candidates/rules but keeps the shared schema.
        control = pipeline.planner.build_messages(
            PromptBuilder(character).build(history, text), ReplyTarget(text)
        )
        differences.append(
            pipeline.last_diagnostics.planner_input_characters
            - sum(len(m["content"]) for m in control)
        )
        selection_ms.append(pipeline.last_diagnostics.trait_selection_seconds * 1000)
        active = [
            t.content
            for t in pipeline.last_trait_candidates
            if t.id in pipeline.last_persona_relevance.active_traits
        ]
        active_sizes.append(len(json.dumps(active, ensure_ascii=False)))
        print(
            json.dumps(
                dict(
                    case=case["id"],
                    input=text,
                    persona_relevance=pipeline.last_persona_relevance.model_dump(),
                    active_traits=active,
                    guidance=planner.guidance.model_dump(),
                    final=result,
                    diagnostics=asdict(pipeline.last_diagnostics),
                ),
                ensure_ascii=False,
            )
        )
    start = perf_counter()
    for _ in range(1000):
        select_trait_candidates(character, "要不要看恐怖片？")
    print(
        json.dumps(
            dict(
                cases=len(cases),
                calls_per_case=2,
                planner_character_delta_control=dict(
                    min=min(differences), mean=mean(differences), max=max(differences)
                ),
                active_projection_characters=dict(
                    min=min(active_sizes),
                    mean=mean(active_sizes),
                    max=max(active_sizes),
                ),
                selector_mean_ms=mean(selection_ms),
                selector_1000_calls_ms=(perf_counter() - start) * 1000,
                tokens="not measured; control retains the new shared schema",
                real_api_latency="not measured",
                database="not used",
            ),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
