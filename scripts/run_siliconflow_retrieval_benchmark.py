"""Real API benchmark; optional explicit CPU BGE comparison, never production DB."""

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from evolving_companion.local_env import load_local_env, PROJECT_ROOT
from evolving_companion.siliconflow_benchmark import (
    DEFAULT_DIMENSION,
    RequestStats,
    benchmark_setup,
    create_siliconflow_providers,
    render_report,
    run_profile,
)


def nonnegative_price(value: str) -> float:
    price = float(value)
    if not math.isfinite(price) or price < 0:
        raise argparse.ArgumentTypeError("price must be finite and nonnegative")
    return price


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-local-baseline", action="store_true")
    parser.add_argument(
        "--dimension",
        type=int,
        default=DEFAULT_DIMENSION,
        help="expected response dimension, validated (default: 4096)",
    )
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--embedding-price-per-million", type=nonnegative_price)
    parser.add_argument("--reranker-price-per-million", type=nonnegative_price)
    parser.add_argument("--currency", default="unspecified")
    parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "runtime/benchmarks"
    )
    args = parser.parse_args()
    load_local_env()
    try:
        embedding, reranker, embedding_stats, reranker_stats = (
            create_siliconflow_providers(dimension=args.dimension, timeout=args.timeout)
        )
    except ValueError as error:
        print(error)
        return 1
    result = {
        "setup": benchmark_setup(),
        "decision": "INCONCLUSIVE",
        "currency": args.currency,
        "profiles": [],
    }
    api_result = run_profile(
        "siliconflow-qwen3", embedding, reranker, embedding_stats, reranker_stats
    )
    api_result["embedding_summary"] = embedding_stats.summary(
        args.embedding_price_per_million
    )
    api_result["reranker_summary"] = reranker_stats.summary(
        args.reranker_price_per_million
    )
    api_result["embedding_requests"] = embedding_stats.records
    api_result["reranker_requests"] = reranker_stats.records
    result["profiles"].append(api_result)
    if args.with_local_baseline:
        # No local initialization or ML imports in API-only mode.
        from evolving_companion.embeddings import LocalBGEEmbeddingProvider
        from evolving_companion.memory_providers import LocalBGERerankerProvider

        local_embedding_stats, local_reranker_stats = RequestStats(), RequestStats()
        local_result = run_profile(
            "local-bge-cpu",
            LocalBGEEmbeddingProvider(),
            LocalBGERerankerProvider(),
            local_embedding_stats,
            local_reranker_stats,
            local=True,
        )
        local_result["embedding_summary"] = local_embedding_stats.summary()
        local_result["reranker_summary"] = local_reranker_stats.summary()
        local_result["embedding_requests"] = local_embedding_stats.records
        local_result["reranker_requests"] = local_reranker_stats.records
        result["profiles"].append(local_result)
    report = render_report(result)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = "a6_1_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    json_path, markdown_path = (
        args.output_dir / f"{stem}.json",
        args.output_dir / f"{stem}.md",
    )
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    markdown_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"JSON: {json_path}\nMarkdown: {markdown_path}")
    return 0 if all(profile["complete"] for profile in result["profiles"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
