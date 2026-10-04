"""Offline same-input full/simplified ablation; synthetic data, no provider requests."""

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from random import Random
from statistics import mean
from tempfile import TemporaryDirectory
from time import perf_counter
from uuid import uuid4, uuid5

from evolving_companion.affective import AffectiveAppraisal, Event
from evolving_companion.affective_store import AffectiveStore
from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_projection import (
    CharacterProjector,
    select_trait_candidates,
)
from evolving_companion.character_life import ProjectedLifeContext
from evolving_companion.expression import ExpressionSelector
from evolving_companion.observation import ObservationSnapshot
from evolving_companion.prompting import Message, PromptBuilder
from evolving_companion.reply_pipeline import NaturalReplyPipeline, Replyer
from evolving_companion.reply_planning import (
    AppraisedReplyGuidance,
    ReplyPlanner,
    ReplyTarget,
)
from evolving_companion.simplified_conversation import (
    AppraisedTinyPlan,
    ConversationState,
    RelevantContextBuilder,
)
from evolving_companion.storage import SQLiteStore
from evolving_companion.time_model import CharacterTimeSnapshot


@dataclass
class MemoryStub:
    memory_type: str
    source: str
    content: str


class Client:
    def __init__(self, output: str) -> None:
        self.output = output
        self.requests: list[list[Message]] = []

    def complete(self, messages: list[Message]) -> str:
        self.requests.append(messages)
        return self.output


def appraisal(kind: str | None = None) -> AffectiveAppraisal:
    return AffectiveAppraisal.model_validate(
        dict(
            event_significance=0.2,
            appraisal=dict(
                relevance=1.0,
                valence=0,
                novelty=0,
                social_meaning="ordinary",
                reality="user_reported",
            ),
            emotion_impulses={} if kind is None else {kind: 0.8},
            relationship_signal=dict(
                meaningful=False, dimensions=[], direction="none", strength="mild"
            ),
            cause_summary="合成烟测事件",
            worth_remembering=False,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pipeline",
        choices=("both", "natural_full", "natural_simplified"),
        default="both",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    seed = load_character_seed_data(root / "data/characters/si_001.yaml")
    character = CharacterProjector().project(seed)
    cases = json.loads(
        (root / "tests/fixtures/simplified_conversation_cases.json").read_text(
            encoding="utf-8"
        )
    )
    now = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
    primary = uuid5(seed.identity.internal_id, "synthetic-primary")
    modes = (
        ("natural_full", "natural_simplified")
        if args.pipeline == "both"
        else (args.pipeline,)
    )
    sizes: dict[str, list[tuple[int, int]]] = {mode: [] for mode in modes}
    context_ms = []
    with TemporaryDirectory(prefix="si-simplified-ab-") as directory:
        for case in cases:
            for mode in modes:
                db = SQLiteStore(Path(directory) / f"{case['id']}-{mode}.db")
                store = AffectiveStore(db.path, seed.identity.internal_id, primary, now)
                actor = (
                    primary
                    if case.get("familiar", True)
                    else uuid5(primary, "synthetic-stranger")
                )
                kind = case.get("affect")
                # Synthetic preconditioning via the existing state API; never SQL edits.
                if kind:
                    for _ in range(30 if kind == "low_energy" else 1):
                        store.apply(
                            Event(
                                id=uuid4(),
                                type="conversation_message",
                                actor=actor,
                                target=seed.identity.internal_id,
                                summary="合成前置事件",
                                timestamp=now,
                                source="archive",
                            ),
                            appraisal("sadness" if kind == "low_energy" else kind),
                        )
                before = store.snapshot(actor, now)
                history = [
                    {"role": "user", "content": text}
                    for text in case.get("history", [])
                ]
                memories = (
                    ()
                    if "memory" not in case
                    else (
                        MemoryStub(
                            "semantic", case.get("source", "explicit"), case["memory"]
                        ),
                    )
                )
                life = ProjectedLifeContext(
                    "student", "学生", "合成住宅", "合成学校", None, "合成客厅"
                )
                observation = ObservationSnapshot(
                    character_id=seed.identity.internal_id,
                    observed_at=now,
                    location_entity_id=uuid5(primary, "synthetic-place"),
                    place_name="合成客厅",
                    status="available",
                    day_period="afternoon",
                )
                time = CharacterTimeSnapshot(
                    now, now, "UTC", now.date(), now.timetz(), None, None
                )
                state = ConversationState(
                    character,
                    history,
                    ReplyTarget(case["text"]),
                    memories,
                    time=time,
                    life=life,
                    observation=observation,
                )
                start = perf_counter()
                selected = RelevantContextBuilder().build(state, before)
                context_ms.append((perf_counter() - start) * 1000)
                tiny = dict(
                    focus="回应当前合成消息",
                    stance=case.get("trait"),
                    boundary=case.get("boundary"),
                    ask=case["ask"],
                )
                if mode == "natural_simplified":
                    plan = AppraisedTinyPlan.model_validate(
                        tiny | appraisal().model_dump()
                    )
                    messages = []
                else:
                    candidates = select_trait_candidates(
                        character,
                        case["text"],
                        history,
                        familiar=case.get("familiar", True),
                    )
                    plan = AppraisedReplyGuidance.model_validate(
                        dict(
                            focus=tiny["focus"],
                            reply_act="explore" if case["ask"] else "react",
                            scene="ordinary",
                            tone="casual",
                            prefer=["direct"],
                            avoid=[],
                            reply_reference="只使用已有立场和事实，不强制续聊。",
                            persona_relevance=dict(
                                active_traits=[t.id for t in candidates[:3]],
                                strength="high" if candidates else "none",
                                reason="合成选择，非模型语义评估。",
                            ),
                        )
                        | appraisal().model_dump()
                    )
                    messages = PromptBuilder(character).build(
                        history,
                        case["text"],
                        memories,
                        character_time=time,
                        character_life_context=life,
                        observation=observation,
                        affective_managed=True,
                        persona_managed=True,
                    )
                planner, replyer = (
                    Client(plan.model_dump_json()),
                    Client("[synthetic reply；仅验证表达路径]"),
                )
                pipeline = NaturalReplyPipeline(
                    ReplyPlanner(planner),
                    Replyer(replyer),
                    ExpressionSelector(rng=Random(1)),
                    store,
                    simplified=mode == "natural_simplified",
                )
                reply = pipeline.reply(
                    messages,
                    state.target,
                    Event(
                        id=uuid4(),
                        type="conversation_message",
                        actor=actor,
                        target=seed.identity.internal_id,
                        summary="合成当前事件",
                        timestamp=now,
                        source="archive",
                    ),
                    character=character,
                    state=state,
                )
                assert len(planner.requests) == len(replyer.requests) == 1
                assert pipeline.last_diagnostics is not None
                assert (
                    pipeline.last_diagnostics.planner_error is None
                    and pipeline.last_diagnostics.affective_error is None
                )
                sizes[mode].append(
                    (
                        sum(len(m["content"]) for m in planner.requests[0]),
                        sum(len(m["content"]) for m in replyer.requests[0]),
                    )
                )
                print(
                    json.dumps(
                        dict(
                            case=case["id"],
                            pipeline=mode,
                            selected_context=(
                                pipeline.last_relevant_context or selected
                            ).payload()
                            if mode == "natural_simplified"
                            else messages,
                            planner_output=plan.model_dump(mode="json"),
                            planner_expression_fields=4
                            if mode == "natural_simplified"
                            else 8,
                            downstream_expression_controls=0
                            if mode == "natural_simplified"
                            else 4,
                            prompt_characters=dict(
                                planner=sizes[mode][-1][0], replyer=sizes[mode][-1][1]
                            ),
                            final=reply,
                            diagnostics=asdict(pipeline.last_diagnostics),
                        ),
                        ensure_ascii=False,
                    )
                )
    if args.pipeline == "both":
        assert all(
            simple[0] < full[0] and simple[1] < full[1]
            for full, simple in zip(
                sizes["natural_full"], sizes["natural_simplified"], strict=True
            )
        )
    print(
        json.dumps(
            dict(
                cases=len(cases),
                modes=modes,
                calls_per_turn=2,
                character_summary={
                    mode: dict(
                        planner_mean=mean(x[0] for x in pairs),
                        replyer_mean=mean(x[1] for x in pairs),
                        planner_min=min(x[0] for x in pairs),
                        planner_max=max(x[0] for x in pairs),
                        replyer_min=min(x[1] for x in pairs),
                        replyer_max=max(x[1] for x in pairs),
                    )
                    for mode, pairs in sizes.items()
                },
                local_context_mean_ms=mean(context_ms),
                tokens="not measured",
                real_latency="not measured",
                naturalness="requires real QQ validation; fake plans are scripted",
                temporary_database="removed",
            ),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
