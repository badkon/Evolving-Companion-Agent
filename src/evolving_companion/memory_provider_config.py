"""Small entry-point-only factory; no import-time environment or model loading."""

import os

from evolving_companion.embeddings import LocalBGEEmbeddingProvider, MODEL_NAME
from evolving_companion.memory_providers import (
    APIEmbeddingProvider,
    APIRerankerProvider,
    EmbeddingProvider,
    LocalBGERerankerProvider,
    RerankerProvider,
)


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value.strip():
        raise ValueError(f"{name} is required for the selected memory API provider")
    return value


def create_memory_providers() -> tuple[EmbeddingProvider, RerankerProvider]:
    embedding_kind = os.environ.get("SI_MEMORY_EMBEDDING_PROVIDER", "local")
    reranker_kind = os.environ.get("SI_MEMORY_RERANKER_PROVIDER", "local")
    embedding: EmbeddingProvider
    reranker: RerankerProvider
    if embedding_kind == "local":
        if os.environ.get("SI_MEMORY_EMBEDDING_MODEL", MODEL_NAME) != MODEL_NAME:
            raise ValueError("local embedding supports only the existing BGE model")
        embedding = LocalBGEEmbeddingProvider()
    elif embedding_kind == "api":
        embedding = APIEmbeddingProvider(
            provider_id=_required("SI_MEMORY_EMBEDDING_API_ID"),
            model_id=_required("SI_MEMORY_EMBEDDING_MODEL"),
            dimension=int(_required("SI_MEMORY_EMBEDDING_DIMENSION")),
            endpoint=_required("SI_MEMORY_EMBEDDING_API_URL"),
            api_key=_required("SI_MEMORY_EMBEDDING_API_KEY"),
            timeout=float(os.environ.get("SI_MEMORY_API_TIMEOUT", "30")),
            batch_size=int(os.environ.get("SI_MEMORY_EMBEDDING_BATCH_SIZE", "32")),
        )
    else:
        raise ValueError("SI_MEMORY_EMBEDDING_PROVIDER must be local or api")
    if reranker_kind == "local":
        if (
            os.environ.get(
                "SI_MEMORY_RERANKER_MODEL", LocalBGERerankerProvider.model_id
            )
            != LocalBGERerankerProvider.model_id
        ):
            raise ValueError("local reranker supports only the existing BGE model")
        reranker = LocalBGERerankerProvider()
    elif reranker_kind == "api":
        reranker = APIRerankerProvider(
            model_id=_required("SI_MEMORY_RERANKER_MODEL"),
            endpoint=_required("SI_MEMORY_RERANKER_API_URL"),
            api_key=_required("SI_MEMORY_RERANKER_API_KEY"),
            timeout=float(os.environ.get("SI_MEMORY_API_TIMEOUT", "30")),
        )
    else:
        raise ValueError("SI_MEMORY_RERANKER_PROVIDER must be local or api")
    return embedding, reranker
