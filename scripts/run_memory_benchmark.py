"""Run the manual, real-API Memory Extraction benchmark fixture."""

import json
import os
from pathlib import Path
from typing import Any

from openai import APIError
from pydantic import ValidationError

from evolving_companion.llm import LLMClient
from evolving_companion.local_env import load_local_env
from evolving_companion.memory_extraction import MemoryExtractor

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "memory_extraction_cases.json"
)
DECISIONS = ("save", "reject", "uncertain")


def _safe_error(error: Exception) -> str:
    message = str(error)
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if api_key:
        message = message.replace(api_key, "[REDACTED]")
    return f"{type(error).__name__}: {message}"


def main() -> int:
    if not load_local_env():
        print(
            "DEEPSEEK_API_KEY is not configured. Set it in the environment or "
            "create .env.local from .env.example."
        )
        return 1
    with FIXTURE_PATH.open(encoding="utf-8") as fixture_file:
        cases: list[dict[str, Any]] = json.load(fixture_file)["cases"]

    try:
        extractor = MemoryExtractor(LLMClient(max_retries=0))
    except Exception as error:
        print(f"Unable to initialize DeepSeek client: {_safe_error(error)}")
        return 1

    successful_calls = 0
    api_failures = 0
    extraction_errors = 0
    candidate_count = 0
    decision_counts = dict.fromkeys(DECISIONS, 0)

    for index, case in enumerate(cases, start=1):
        print(f"\n{'=' * 72}")
        print(f"Case {index}/{len(cases)}: {case['name']}")
        print("Input messages:")
        print(json.dumps(case["messages"], ensure_ascii=False, indent=2))
        expected = {
            key: value for key, value in case.items() if key not in {"name", "messages"}
        }
        print("Expected:")
        print(json.dumps(expected, ensure_ascii=False, indent=2))

        try:
            result = extractor.extract_memories(case["messages"])
        except APIError as error:
            api_failures += 1
            print(f"API error: {_safe_error(error)}")
            continue
        except ValidationError as error:
            extraction_errors += 1
            print(f"Extraction result validation error: {_safe_error(error)}")
            continue
        except Exception as error:
            extraction_errors += 1
            print(f"Extraction error: {_safe_error(error)}")
            continue

        successful_calls += 1
        candidate_count += len(result.candidates)
        for candidate in result.candidates:
            decision_counts[candidate.decision] += 1

        print("Actual candidates:")
        if not result.candidates:
            print("  candidates: []")
        else:
            for candidate_index, candidate in enumerate(result.candidates, start=1):
                print(f"  Candidate {candidate_index}:")
                for field, value in candidate.model_dump().items():
                    print(f"    {field}: {json.dumps(value, ensure_ascii=False)}")

    print(f"\n{'=' * 72}")
    print("Benchmark summary")
    print(f"Total cases: {len(cases)}")
    print(f"Successful calls: {successful_calls}")
    print(f"API failures: {api_failures}")
    print(f"Extraction errors: {extraction_errors}")
    print(f"Total candidates: {candidate_count}")
    for decision, count in decision_counts.items():
        print(f"{decision}: {count}")

    return 0 if api_failures == 0 and extraction_errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
