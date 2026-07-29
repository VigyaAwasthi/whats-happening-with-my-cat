"""Memory retrieval and write/compaction services."""

import logging
from typing import Protocol
from uuid import UUID

from app.ingestion.embeddings import EmbeddingProvider
from app.memory.repository import MemoryRepository, profile_facts
from app.schemas.enums import Corner, MessageRole, ToolErrorCode
from app.schemas.llm import MemorySummary
from app.schemas.memory import MemoryResult
from app.tools.contracts import (
    MemoryRetriever,
    MemoryRetrieverInput,
    MemoryRetrieverOutput,
    ToolError,
)

logger = logging.getLogger(__name__)


class MemorySummarizer(Protocol):
    """Fast-model structured summarization boundary."""

    async def summarize(self, messages: list[str]) -> MemorySummary | None:
        """Return a validated summary or null after fail-closed retries."""
        ...


class PostgresMemoryRetriever(MemoryRetriever):
    """Phase 1 memory tool implementation with repository-level cat filtering."""

    def __init__(
        self, repository: MemoryRepository, embedder: EmbeddingProvider
    ) -> None:
        self._repository = repository
        self._embedder = embedder

    async def retrieve(self, request: MemoryRetrieverInput) -> MemoryRetrieverOutput:
        try:
            embedding = await self._embedder.embed_query(request.query)
            profile = await self._repository.fetch_profile(request.cat_id)
            summaries = await self._repository.search_long_term(
                request.cat_id, embedding, request.limit
            )
            return MemoryRetrieverOutput(
                result=MemoryResult(
                    cat_id=request.cat_id,
                    profile_facts=profile_facts(profile),
                    relevant_summaries=summaries,
                )
            )
        except Exception as exc:
            logger.exception("cat-scoped memory retrieval failed")
            return MemoryRetrieverOutput(
                error=ToolError(
                    code=ToolErrorCode.UNAVAILABLE,
                    message=str(exc) or "memory unavailable",
                    retryable=True,
                )
            )


class CatMemoryService:
    """Cat-scoped session writes, rolling compaction, and long-term summaries."""

    def __init__(
        self,
        repository: MemoryRepository,
        embedder: EmbeddingProvider,
        summarizer: MemorySummarizer,
        *,
        summary_message_limit: int,
    ) -> None:
        self._repository = repository
        self._embedder = embedder
        self._summarizer = summarizer
        self._limit = summary_message_limit

    async def record_exchange(
        self,
        *,
        cat_id: UUID,
        requested_session_id: UUID,
        corner: Corner,
        user_message: str,
        assistant_message: str,
    ) -> UUID:
        """Write both sides under one cat scope and compact when over threshold."""
        session_id = await self._repository.ensure_session(
            cat_id, requested_session_id, corner
        )
        await self._repository.append_message(
            cat_id, session_id, MessageRole.USER, user_message
        )
        await self._repository.append_message(
            cat_id, session_id, MessageRole.ASSISTANT, assistant_message
        )
        await self._compact_if_needed(cat_id, session_id)
        return session_id

    async def end_session(self, cat_id: UUID, session_id: UUID) -> None:
        """Write one validated long-term summary for the same cat."""
        messages = await self._repository.session_messages(cat_id, session_id)
        if not messages:
            return
        summary = await self._summarizer.summarize(
            [f"{message.role.value}: {message.content}" for message in messages]
        )
        if summary is None:
            return
        embedding = await self._embedder.embed_query(summary.summary)
        await self._repository.write_long_term(
            cat_id, session_id, summary.summary, embedding
        )

    async def working_context(
        self, cat_id: UUID, session_id: UUID, corner: Corner
    ) -> list[str]:
        """Return current-session context only from the exact cat/corner scope."""
        summary = await self._repository.rolling_summary(
            cat_id, session_id, corner
        )
        messages = await self._repository.session_messages(
            cat_id, session_id, corner
        )
        context = [] if summary is None else [f"rolling summary: {summary}"]
        context.extend(
            f"{message.role.value}: {message.content}" for message in messages
        )
        return context

    async def _compact_if_needed(self, cat_id: UUID, session_id: UUID) -> None:
        messages = await self._repository.session_messages(cat_id, session_id)
        if len(messages) <= self._limit:
            return
        keep = max(2, self._limit // 2)
        oldest = messages[:-keep]
        summary = await self._summarizer.summarize(
            [f"{message.role.value}: {message.content}" for message in oldest]
        )
        if summary is None:
            return
        await self._repository.update_rolling_summary(
            cat_id,
            session_id,
            summary.summary,
            [message.id for message in oldest],
        )
