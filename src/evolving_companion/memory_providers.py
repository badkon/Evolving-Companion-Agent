"""Memory-only provider contracts, local reranking and explicit HTTP adapters."""

from __future__ import annotations

from collections.abc import Sequence
import math
from typing import Any, Protocol

import httpx
import numpy as np


class EmbeddingProvider(Protocol):
    provider_id: str
    model_id: str
    dimension: int

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray: ...


class RerankerProvider(Protocol):
    def score(self, query: str, texts: Sequence[str]) -> Sequence[float]: ...


class MemoryProviderError(RuntimeError):
    """Safe diagnostic: never include URL, credentials or response body."""


class _APIEndpoint:
    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model_id: str,
        timeout: float = 30,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        try:
            url = httpx.URL(endpoint)
        except httpx.InvalidURL:
            raise ValueError("invalid memory API endpoint") from None
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.userinfo
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "memory API endpoint must be an HTTP(S) URL without credentials"
            )
        if not api_key or not model_id.strip():
            raise ValueError("memory API requires a key and model")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("memory API timeout must be positive and finite")
        self.model_id = model_id
        self._endpoint = endpoint
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport

    def _post(self, payload: dict[str, Any]) -> Any:
        # Short-lived client has explicit ownership; no persistent HTTP resource.
        try:
            with httpx.Client(
                timeout=self._timeout, transport=self._transport, follow_redirects=False
            ) as client:
                response = client.post(
                    self._endpoint,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={"model": self.model_id, **payload},
                )
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException:
            raise MemoryProviderError("memory API request timed out") from None
        except httpx.HTTPStatusError as error:
            raise MemoryProviderError(
                f"memory API returned HTTP {error.response.status_code}"
            ) from None
        except httpx.HTTPError:
            raise MemoryProviderError("memory API request failed") from None
        except ValueError:
            raise MemoryProviderError("memory API returned invalid JSON") from None


class APIEmbeddingProvider(_APIEndpoint):
    """POST model/input; response data entries have index and embedding."""

    def __init__(
        self,
        *,
        provider_id: str,
        dimension: int,
        endpoint: str,
        api_key: str,
        model_id: str,
        timeout: float = 30,
        transport: httpx.BaseTransport | None = None,
        batch_size: int = 32,
    ) -> None:
        super().__init__(
            endpoint=endpoint,
            api_key=api_key,
            model_id=model_id,
            timeout=timeout,
            transport=transport,
        )
        if not provider_id.strip() or dimension <= 0 or batch_size <= 0:
            raise ValueError(
                "embedding provider ID, dimension and batch size are required"
            )
        self.provider_id = provider_id
        self.dimension = dimension
        self._batch_size = batch_size

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        items = list(texts)
        if any(not text.strip() for text in items):
            raise ValueError("embedding text must not be empty")
        batches = []
        for start in range(0, len(items), self._batch_size):
            batch = items[start : start + self._batch_size]
            response = self._post({"input": batch, "encoding_format": "float"})
            try:
                rows = sorted(response["data"], key=lambda row: row["index"])
                if [row["index"] for row in rows] != list(range(len(batch))):
                    raise ValueError("invalid indices")
                vectors = np.asarray(
                    [row["embedding"] for row in rows], dtype=np.float32
                )
                if vectors.shape != (len(batch), self.dimension):
                    raise ValueError("invalid dimensions")
                norms = np.linalg.norm(vectors, axis=1, keepdims=True)
                if (
                    not np.isfinite(vectors).all()
                    or not np.isfinite(norms).all()
                    or np.any(norms == 0)
                ):
                    raise ValueError("invalid vectors")
                batches.append(np.asarray(vectors / norms, dtype=np.float32))
            except (KeyError, TypeError, ValueError, OverflowError):
                raise MemoryProviderError(
                    "embedding API returned invalid vectors or indices"
                ) from None
        return (
            np.concatenate(batches)
            if batches
            else np.empty((0, self.dimension), dtype=np.float32)
        )


class APIRerankerProvider(_APIEndpoint):
    """One candidate-set request; results must cover every input index."""

    def score(self, query: str, texts: Sequence[str]) -> Sequence[float]:
        if not texts:
            return []
        response = self._post(
            {"query": query, "documents": list(texts), "top_n": len(texts)}
        )
        try:
            rows = sorted(response["results"], key=lambda row: row["index"])
            if [row["index"] for row in rows] != list(range(len(texts))):
                raise ValueError("invalid indices")
            scores = [float(row["relevance_score"]) for row in rows]
            if not all(math.isfinite(score) for score in scores):
                raise ValueError("non-finite scores")
            return scores
        except (KeyError, TypeError, ValueError, OverflowError):
            raise MemoryProviderError(
                "reranker API returned invalid scores or indices"
            ) from None


class LocalBGERerankerProvider:
    model_id = "BAAI/bge-reranker-base"
    _model: Any = None

    def score(self, query: str, texts: Sequence[str]) -> Sequence[float]:
        if not texts:
            return []
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_id, device="cpu")
        from torch.nn import Identity

        values = self._model.predict(
            [(query, text) for text in texts],
            activation_fn=Identity(),
            batch_size=10,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return np.asarray(values).reshape(-1).tolist()
