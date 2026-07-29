"""Voyage and zero-cost deterministic embedding providers."""

import asyncio
import hashlib
import logging
from collections import OrderedDict
from typing import Protocol

import httpx
from pydantic import Field, NonNegativeInt
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.schemas.base import ContractModel

logger = logging.getLogger(__name__)
_RATE_LIMIT_FALLBACK_SECONDS = 20.5


class EmbeddingBatch(ContractModel):
    """One result per requested text, preserving failures as null vectors."""

    vectors: list[list[float] | None] = Field(
        description="Embedding aligned by index with the requested texts."
    )
    failures: NonNegativeInt = Field(description="Number of null vectors.")


class EmbeddingProvider(Protocol):
    """Typed embedding interface shared by ingestion and query retrieval."""

    dimensions: int

    async def embed_documents(self, texts: list[str]) -> EmbeddingBatch:
        """Embed document text without allowing service exceptions to escape."""
        ...

    async def embed_query(self, text: str) -> list[float] | None:
        """Embed one query or return null after typed/logged failure."""
        ...


class VoyageEmbeddingProvider:
    """Batched Voyage embeddings with bounded exponential retry."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        dimensions: int,
        client: httpx.AsyncClient | None = None,
        batch_size: int = 24,
    ) -> None:
        self.dimensions = dimensions
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.AsyncClient(
            base_url="https://api.voyageai.com", timeout=30
        )
        self._batch_size = batch_size
        self._query_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._query_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._next_request_at = 0.0
        self._minimum_request_interval = 0.0

    async def embed_documents(self, texts: list[str]) -> EmbeddingBatch:
        vectors: list[list[float] | None] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            result = await self._embed_batch(batch, input_type="document")
            vectors.extend(result)
        return EmbeddingBatch(
            vectors=vectors,
            failures=sum(vector is None for vector in vectors),
        )

    async def embed_query(self, text: str) -> list[float] | None:
        async with self._query_lock:
            cached = self._query_cache.get(text)
            if cached is not None:
                self._query_cache.move_to_end(text)
                return list(cached)
            result = (await self._embed_batch([text], input_type="query"))[0]
            if result is not None:
                self._query_cache[text] = list(result)
                self._query_cache.move_to_end(text)
                while len(self._query_cache) > 128:
                    self._query_cache.popitem(last=False)
            return result

    async def _embed_batch(
        self, texts: list[str], *, input_type: str
    ) -> list[list[float] | None]:
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(4),
                wait=wait_exponential(multiplier=0.25, min=0.25, max=4),
                retry=retry_if_exception_type((httpx.HTTPError, ValueError)),
                reraise=True,
            ):
                with attempt:
                    response = await self._post_embeddings(texts, input_type)
                    if response.status_code == 429:
                        logger.warning(
                            "Voyage rate limit reached; the next retry is paced"
                        )
                    response.raise_for_status()
                    payload = response.json()
                    vectors = [
                        item["embedding"]
                        for item in sorted(payload["data"], key=lambda item: item["index"])
                    ]
                    if len(vectors) != len(texts):
                        raise ValueError("Voyage returned the wrong number of embeddings")
                    if any(len(vector) != self.dimensions for vector in vectors):
                        raise ValueError("Voyage embedding dimension mismatch")
                    return vectors
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.error("embedding batch failed closed: %s", exc)
            return [None] * len(texts)
        return [None] * len(texts)

    async def _post_embeddings(
        self, texts: list[str], input_type: str
    ) -> httpx.Response:
        """Serialize requests and retain reduced-tier pacing after the first 429."""
        async with self._request_lock:
            loop = asyncio.get_running_loop()
            delay = max(0.0, self._next_request_at - loop.time())
            if delay:
                await asyncio.sleep(delay)
            response = await self._client.post(
                "/v1/embeddings",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "input": texts,
                    "model": self._model,
                    "input_type": input_type,
                    "truncation": False,
                },
            )
            now = loop.time()
            if response.status_code == 429:
                retry_delay = _rate_limit_delay(response)
                if response.headers.get("retry-after") is None:
                    self._minimum_request_interval = max(
                        self._minimum_request_interval,
                        _RATE_LIMIT_FALLBACK_SECONDS,
                    )
                self._next_request_at = now + retry_delay
            else:
                self._next_request_at = now + self._minimum_request_interval
            return response


def _rate_limit_delay(response: httpx.Response) -> float:
    """Honor Retry-After, or pace retries for Voyage's reduced 3-RPM tier."""
    raw = response.headers.get("retry-after")
    if raw is not None:
        try:
            return max(0.25, float(raw))
        except ValueError:
            pass
    return _RATE_LIMIT_FALLBACK_SECONDS


class DeterministicEmbeddingProvider:
    """Zero-cost deterministic vectors for development and tests, not production."""

    def __init__(self, dimensions: int = 1024) -> None:
        self.dimensions = dimensions

    async def embed_documents(self, texts: list[str]) -> EmbeddingBatch:
        vectors: list[list[float] | None] = [
            self._embed(text) for text in texts
        ]
        await asyncio.sleep(0)
        return EmbeddingBatch(vectors=vectors, failures=0)

    async def embed_query(self, text: str) -> list[float]:
        await asyncio.sleep(0)
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        digest = hashlib.shake_256(text.casefold().encode("utf-8")).digest(
            self.dimensions
        )
        return [(byte - 127.5) / 127.5 for byte in digest]
