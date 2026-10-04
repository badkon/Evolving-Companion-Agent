"""Offline OneBot image → real Conversation → fake Planner/Replyer smoke.

All images, responses, credentials and observations are synthetic. No network.
"""

from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import httpx

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.clock import FixedClock
from evolving_companion.conversation import Conversation
from evolving_companion.media_input import (
    MediaFetcher,
    MediaReference,
    VisionInputService,
)
from evolving_companion.mini_life import MiniLifeService
from evolving_companion.prompting import Message
from evolving_companion.qq_adapter import QQPrivateChatAdapter
from evolving_companion.reply_pipeline import NaturalReplyPipeline, Replyer
from evolving_companion.reply_planning import ReplyPlanner
from evolving_companion.storage import SQLiteStore
from evolving_companion.vision import ImageData, VisualObservation


class FakeVisionProvider:
    def __init__(self, observation: VisualObservation, fail: bool):
        self.observation, self.fail = observation, fail
        self.seconds = 0.0
        self.calls = 0

    def analyze_image(self, image: ImageData, user_text: str = "") -> VisualObservation:
        started = perf_counter()
        self.calls += 1
        try:
            if self.fail:
                raise TimeoutError("synthetic failure")
            return VisualObservation.model_validate(self.observation.model_dump())
        finally:
            self.seconds += perf_counter() - started


class TimedFetcher(MediaFetcher):
    seconds = 0.0

    def fetch(self, reference: MediaReference) -> ImageData:
        started = perf_counter()
        try:
            return super().fetch(reference)
        finally:
            self.seconds += perf_counter() - started


class FakeClient:
    def __init__(self, response: str):
        self.response = response
        self.requests: list[list[Message]] = []

    def complete(self, messages: list[Message]) -> str:
        self.requests.append(messages)
        return self.response


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    seed = load_character_seed_data(root / "data/characters/si_001.yaml")
    cases = json.loads(
        (root / "tests/fixtures/vision_cases.json").read_text(encoding="utf-8")
    )
    cases = {case.pop("id"): case for case in cases}
    plan = '{"focus":"回应本轮展示","stance":null,"boundary":null,"ask":false}'
    with (
        TemporaryDirectory(prefix="si-vision-smoke-") as directory,
        ExitStack() as resources,
    ):
        for index, (name, text, fail) in enumerate(
            [
                ("photo", "", False),
                ("tired_cat", "", False),
                ("celebrate", "今天就是这个状态", False),
                ("photo", "", True),
            ]
        ):
            fetcher = TimedFetcher(
                resolver=lambda _: ("8.8.8.8",),
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(
                        200,
                        content=b"\x89PNG\r\n\x1a\nsynthetic",
                        headers={"content-type": "image/png"},
                    )
                ),
            )
            resources.callback(fetcher.close)
            provider = FakeVisionProvider(
                VisualObservation.model_validate(cases[name]), fail
            )
            planner, reply = (
                FakeClient(plan),
                FakeClient("合成 Replyer 回复，不评估真实台词质量。"),
            )
            core = Conversation(
                reply,
                CharacterProjector().project(seed),
                SQLiteStore(Path(directory) / f"case-{index}.db"),
                reply_pipeline=NaturalReplyPipeline(
                    ReplyPlanner(planner), Replyer(reply), simplified=True
                ),
                mini_life_service=MiniLifeService(seed),
                clock=FixedClock(datetime(2026, 10, 5, 12, tzinfo=timezone.utc)),
            )
            adapter = QQPrivateChatAdapter(
                core,
                allowed_user_ids={101},
                bot_user_id=202,
                vision_input=VisionInputService(provider, fetcher),
            )
            result = adapter.handle_event(
                dict(
                    post_type="message",
                    message_type="private",
                    sub_type="friend",
                    user_id=101,
                    message_id=index + 1,
                    message=[
                        {"type": "text", "data": {"text": text}},
                        {
                            "type": "image",
                            "data": {"url": "https://gchat.qpic.cn/synthetic"},
                        },
                    ],
                )
            )
            assert result.status == "handled" and result.response is not None
            assert provider.calls == len(planner.requests) == len(reply.requests) == 1
            payload = json.loads(planner.requests[0][-1]["content"])
            visual = payload["life_world"]["visual_observation"]
            before = json.dumps(payload, ensure_ascii=False)
            without = dict(
                payload,
                life_world={
                    k: v
                    for k, v in payload["life_world"].items()
                    if k not in {"visual_observation", "visual_scope"}
                },
            )
            added = len(before) - len(json.dumps(without, ensure_ascii=False))
            print(
                json.dumps(
                    dict(
                        case=name,
                        failure=fail,
                        media_type=cases[name]["media_type"],
                        observation=visual,
                        relevant_context=payload,
                        tiny_plan=json.loads(plan),
                        fake_reply_path="OneBot → Vision → Conversation → TinyPlanner → Replyer",
                        response=result.response.text,
                        preprocessing_ms=round(fetcher.seconds * 1000, 3),
                        fake_provider_ms=round(provider.seconds * 1000, 3),
                        context_added_characters=added,
                        main_text_calls=len(planner.requests) + len(reply.requests),
                        new_visual_database_writes=0,
                    ),
                    ensure_ascii=False,
                )
            )
    print(
        "Vision Input + QQ image adapter smoke: 4 passed; temporary databases removed; external API calls: 0"
    )


if __name__ == "__main__":
    main()
