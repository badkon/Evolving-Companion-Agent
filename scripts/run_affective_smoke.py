"""Synthetic two-call production-pipeline smoke; temporary DB, no secrets or APIs."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from tempfile import TemporaryDirectory
from uuid import UUID

from evolving_companion.affective import AffectiveAppraisal
from evolving_companion.affective_store import AffectiveStore
from evolving_companion.character_projection import ProjectedCharacterContext
from evolving_companion.clock import FixedClock
from evolving_companion.conversation import Conversation
from evolving_companion.qq_adapter import relation_target_id
from evolving_companion.reply_pipeline import NaturalReplyPipeline, Replyer
from evolving_companion.reply_planning import (
    AppraisedReplyGuidance,
    ExpressionIntent,
    ReplyPlanner,
)
from evolving_companion.storage import SQLiteStore


class Client:
    def __init__(self, output="（合成 smoke 回复）"):
        self.output = output
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        return self.output


def main():
    character_id = UUID("235fe144-519a-4ed0-a36b-670a2c72311f")
    target = relation_target_id(101)
    clock = FixedClock(datetime(2026, 10, 4, 12, tzinfo=timezone.utc))
    timings, extra_chars = [], []
    with TemporaryDirectory(prefix="si-affective-") as temporary:
        archive = SQLiteStore(Path(temporary) / "smoke.db")
        store = AffectiveStore(archive.path, character_id, target, clock.now_utc())
        planner, replyer = Client(), Client()
        pipeline = NaturalReplyPipeline(
            ReplyPlanner(planner), Replyer(replyer), affective_store=store
        )
        core = Conversation(
            replyer,
            ProjectedCharacterContext("玲；不伪造经历或现实共享空间。"),
            archive,
            character_id=character_id,
            character_timezone="UTC",
            clock=clock,
            reply_pipeline=pipeline,
        )
        cases = [
            ("你好", "ordinary", 0.05, {"joy": 0.08}, False, "none", "mild"),
            (
                "代码终于跑通了",
                "achievement",
                0.4,
                {"joy": 0.6, "relief": 0.5},
                False,
                "none",
                "mild",
            ),
            (
                "我愿意说一件重要私人经历",
                "personal_disclosure",
                0.8,
                {"affection": 0.35},
                True,
                "positive",
                "moderate",
            ),
            (
                "今天临时改一下计划",
                "temporary_change",
                0.3,
                {"annoyance": 0.2},
                False,
                "none",
                "mild",
            ),
            (
                "合成场景：已经严重违反了明确承诺",
                "broken_commitment",
                0.95,
                {"disappointment": 0.7},
                True,
                "negative",
                "strong",
            ),
        ]
        for (
            text,
            meaning,
            significance,
            emotions,
            meaningful,
            direction,
            strength,
        ) in cases:
            before = store.snapshot(target, clock.now_utc())
            appraisal = AffectiveAppraisal.model_validate(
                dict(
                    event_significance=significance,
                    appraisal=dict(
                        relevance=0.9,
                        valence=-0.5 if direction == "negative" else 0.5,
                        novelty=0.2,
                        social_meaning=meaning,
                        reality="user_reported",
                    ),
                    emotion_impulses=emotions,
                    relationship_signal=dict(
                        meaningful=meaningful,
                        dimensions=["trust"]
                        if direction == "negative"
                        else ["closeness"],
                        direction=direction,
                        strength=strength,
                    ),
                    cause_summary="合成测试事件",
                    worth_remembering=significance >= 0.8,
                )
            )
            plan = AppraisedReplyGuidance.model_validate(
                dict(
                    focus="回应当前合成事件",
                    reply_act="react",
                    scene="ordinary",
                    tone="casual",
                    prefer=["reaction"],
                    avoid=["curiosity"],
                    reply_reference="直接回应，不自动续聊。",
                    **appraisal.model_dump(),
                )
            )
            planner.output = plan.model_dump_json()
            base = plan.model_dump_json(
                include=set(ExpressionIntent.model_fields) | {"reply_reference"}
            )
            extra_chars.append(len(planner.output) - len(base))
            assert core.send(text) == replyer.output
            after = store.snapshot(target, clock.now_utc())
            diagnostic = pipeline.last_diagnostics
            assert (
                diagnostic is not None
                and diagnostic.planner_error is None
                and diagnostic.affective_error is None
            )
            timings.append(diagnostic.affective_seconds * 1000)
            print(
                f"{meaning}: emotion={len(after.emotions)} mood_valence={after.mood.valence:.4f} trust_delta={after.relationship.trust - before.relationship.trust:.4f} closeness_delta={after.relationship.closeness - before.relationship.closeness:.4f}"
            )
            if not meaningful:
                assert after.relationship == before.relationship
            clock.advance(minutes=1)
        assert planner.calls == replyer.calls == len(cases)
        restored = AffectiveStore(archive.path, character_id, target, clock.now_utc())
        assert restored.snapshot(target, clock.now_utc()) == store.snapshot(
            target, clock.now_utc()
        )
        later = restored.snapshot(target, clock.now_utc() + timedelta(days=1))
        assert later.emotions == ()
        print(
            f"Planner output added characters: {min(extra_chars)}..{max(extra_chars)} (not tokens)"
        )
        print(
            f"Affective SQLite read+update mean: {mean(timings):.3f} ms; max: {max(timings):.3f} ms"
        )
        print(
            "Main LLM calls: 2 per turn (fake); real token/network latency not measured."
        )
    print("Restart/decay verified; temporary DB removed. Real QQ validation required.")


if __name__ == "__main__":
    main()
