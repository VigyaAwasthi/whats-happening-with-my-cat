"""Selectable hosted and local cross-encoder rerankers."""

import asyncio
import logging
import math
from typing import Any, Protocol

import httpx
from pydantic import Field

from app.schemas.base import ContractModel
from app.schemas.enums import ToolErrorCode
from app.tools.contracts import ToolError

logger = logging.getLogger(__name__)


class RerankItem(ContractModel):
    """One candidate's cross-encoder score."""

    index: int = Field(ge=0, description="Original document index.")
    score: float = Field(description="Cross-encoder relevance score.")


class RerankResult(ContractModel):
    """Ranked items or a typed failure."""

    items: list[RerankItem] = Field(description="Candidates ordered by relevance.")
    error: ToolError | None = Field(
        default=None, description="Typed reranker failure, or null on success."
    )


class Reranker(Protocol):
    """Cross-encoder reranking boundary."""

    async def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> RerankResult:
        """Return ordered cross-encoder scores without raising operational errors."""
        ...


class HostedAPIReranker:
    """Hosted cross-encoder adapter using a conventional JSON API."""

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        model: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=20)

    async def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> RerankResult:
        try:
            response = await self._client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "query": query,
                    "documents": documents,
                    "top_n": top_n,
                },
            )
            response.raise_for_status()
            payload = response.json()
            raw_items = payload.get("results", payload.get("data", []))
            items = [
                RerankItem(
                    index=int(item["index"]),
                    score=_unit_score(
                        float(item.get("relevance_score", item.get("score")))
                    ),
                )
                for item in raw_items
            ]
            return RerankResult(items=items[:top_n])
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            logger.error("hosted reranker failed: %s", exc)
            return _reranker_error("hosted reranker unavailable", retryable=True)


class LocalCrossEncoderReranker:
    """Lazy sentence-transformers CrossEncoder adapter for zero-API-cost use."""

    def __init__(self, model: str) -> None:
        self._model_name = model
        self._model: Any | None = None

    async def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> RerankResult:
        try:
            items = await asyncio.to_thread(
                self._score_sync, query, documents, top_n
            )
            return RerankResult(items=items)
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            logger.error("local reranker failed: %s", exc)
            return _reranker_error(
                "local cross-encoder unavailable; install the local-reranker extra",
                retryable=False,
            )

    def _score_sync(
        self, query: str, documents: list[str], top_n: int
    ) -> list[RerankItem]:
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self._model_name)
        scores = self._model.predict([(query, document) for document in documents])
        ranked = sorted(
            (
                RerankItem(index=index, score=_unit_score(float(score)))
                for index, score in enumerate(scores)
            ),
            key=lambda item: item.score,
            reverse=True,
        )
        return ranked[:top_n]


class TokenOverlapReranker:
    """Deterministic development/test reranker; never selected in production."""

    async def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> RerankResult:
        query_terms = set(query.casefold().split())
        items = [
            RerankItem(
                index=index,
                score=float(len(query_terms & set(document.casefold().split()))),
            )
            for index, document in enumerate(documents)
        ]
        items.sort(key=lambda item: (-item.score, item.index))
        return RerankResult(items=items[:top_n])


def _reranker_error(message: str, *, retryable: bool) -> RerankResult:
    return RerankResult(
        items=[],
        error=ToolError(
            code=ToolErrorCode.UNAVAILABLE,
            message=message,
            retryable=retryable,
        ),
    )


def _unit_score(score: float) -> float:
    """Normalize logit-style cross-encoder output while preserving unit scores."""
    if 0 <= score <= 1:
        return score
    return 1 / (1 + math.exp(-max(-60.0, min(60.0, score))))
