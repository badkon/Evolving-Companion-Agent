"""Offline integration smoke or explicitly requested real, synthetic A/B observation."""

import argparse
from contextlib import ExitStack, closing
from dataclasses import asdict
import json
import os
from pathlib import Path
from random import Random
import sqlite3
from tempfile import TemporaryDirectory

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.conversation import Conversation
from evolving_companion.expression import ExpressionSelector
from evolving_companion.llm import LLMClient
from evolving_companion.local_env import load_local_env
from evolving_companion.prompting import Message
from evolving_companion.reply_pipeline import (
    NaturalReplyPipeline,
    Replyer,
    create_reply_pipeline,
)
from evolving_companion.reply_planning import ReplyGuidance, ReplyPlanner
from evolving_companion.storage import SQLiteStore


class FakeClient:
    def __init__(self, output: str) -> None:
        self.output = output

    def complete(self, messages: list[Message]) -> str:
        return self.output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-llm", action="store_true")
    parser.add_argument("--pipeline", choices=("natural", "legacy"), default="natural")
    args = parser.parse_args()
    if args.real_llm and not load_local_env():
        print("DEEPSEEK_API_KEY is not configured; use the environment or .env.local.")
        return 78
    root = Path(__file__).resolve().parents[1]
    cases = json.loads(
        (root / "tests/fixtures/natural_conversation_cases.json").read_text(
            encoding="utf-8"
        )
    )
    seed = load_character_seed_data(root / "data/characters/si_001.yaml")
    context = CharacterProjector().project(seed)
    print(
        f"mode={'real' if args.real_llm else 'fake'} pipeline={args.pipeline}; synthetic cases only"
    )
    with (
        TemporaryDirectory(prefix="si-natural-reply-") as temporary,
        ExitStack() as resources,
    ):
        store = SQLiteStore(Path(temporary) / "smoke.db")
        live = None
        if args.real_llm:
            live = LLMClient(timeout=30, max_retries=0, max_output_tokens=1024)
            resources.callback(live.close)
        for case in cases:
            client = live or FakeClient("（离线回复占位，不代表自然度验收）")
            pipeline = None
            if args.pipeline == "natural":
                if live:
                    pipeline = create_reply_pipeline(
                        resources,
                        live,
                        os.environ | {"SI_REPLY_PIPELINE": args.pipeline},
                    )
                else:
                    plan = ReplyGuidance.model_validate(
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
                    pipeline = NaturalReplyPipeline(
                        ReplyPlanner(FakeClient(plan.model_dump_json())),
                        Replyer(client),
                        ExpressionSelector(rng=Random(4)),
                    )
            conversation = Conversation(client, context, store, reply_pipeline=pipeline)
            print(f"\n{case['id']} | {case['text']}\nExpected behavior: {case['goal']}")
            try:
                # Establish preference through turns, not production history restore.
                for item in case.get("history", []):
                    if item["role"] == "user":
                        conversation.send(item["content"])
                print(f"Reply: {conversation.send(case['text'])}")
                if pipeline:
                    assert pipeline.last_diagnostics is not None
                    print(
                        json.dumps(
                            asdict(pipeline.last_diagnostics), ensure_ascii=False
                        )
                    )
            except Exception as error:
                print(
                    f"Request failed ({type(error).__name__}); no synthetic success substituted."
                )
                return 1
        with closing(sqlite3.connect(store.path)) as connection:
            assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
            assert connection.execute("SELECT count(*) FROM memories").fetchone() == (
                0,
            )
    print(
        "Smoke completed; temporary DB removed. Real QQ behavioral validation required."
    )
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception as error:
        print(f"Smoke failed ({type(error).__name__}); provider details suppressed.")
        exit_code = 1
    raise SystemExit(exit_code)
