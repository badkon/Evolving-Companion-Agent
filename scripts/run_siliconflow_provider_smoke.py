"""Real SiliconFlow shape/ranking smoke; no Character data or SQLite writes."""

import argparse

from evolving_companion.local_env import load_local_env
from evolving_companion.memory_providers import MemoryProviderError
from evolving_companion.siliconflow_benchmark import (
    DEFAULT_DIMENSION,
    MeasuredEmbedding,
    MeasuredReranker,
    create_siliconflow_providers,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dimension", type=int, default=DEFAULT_DIMENSION)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    load_local_env()
    try:
        embedding, reranker, embedding_stats, reranker_stats = (
            create_siliconflow_providers(dimension=args.dimension, timeout=args.timeout)
        )
    except ValueError as error:
        print(error)
        return 1
    texts = ["用户计划购买 Mac。", "用户周末骑自行车。", "用户喜欢咖啡。"]
    try:
        vector = MeasuredEmbedding(embedding, embedding_stats).embed_texts(
            ["用户计划购买 Mac。"]
        )
        print(
            f"provider={embedding.provider_id} model={embedding.model_id} dimension={vector.shape[1]} shape={vector.shape} dtype={vector.dtype}"
        )
        scores = MeasuredReranker(reranker, reranker_stats).score(
            "用户想买什么电脑？", texts
        )
        print(f"model={reranker.model_id} scores={len(scores)}")
        for index in sorted(
            range(len(texts)), key=lambda index: scores[index], reverse=True
        ):
            print(f"{index}: score={scores[index]:.6f} | {texts[index]}")
    except MemoryProviderError as error:
        print(f"Smoke failed; no retry: {error}")
        return 1
    finally:
        print("Embedding:", embedding_stats.summary())
        print("Reranker:", reranker_stats.summary())
    print("Real SiliconFlow smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
