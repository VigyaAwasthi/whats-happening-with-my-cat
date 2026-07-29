"""SQL repository for parallel lexical and semantic retrieval."""

import asyncio
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import Field, NonNegativeFloat

from app.db import Database, Row, vector_literal
from app.schemas.base import ContractModel
from app.schemas.corpora import BehaviorEntry, HealthEntry
from app.schemas.enums import (
    BehaviorCategory,
    BodySystem,
    ConfidenceLevel,
    UrgencyTier,
)


class CorpusKind(str, Enum):
    """Retrievable parent corpus kinds."""

    HEALTH = "health"
    BEHAVIOR = "behavior"


class CandidateScore(ContractModel):
    """Raw retrieval score for one parent entry."""

    entry_id: str = Field(description="Stable parent entry id.")
    score: NonNegativeFloat = Field(description="Non-negative retrieval score.")


class HybridCandidates(ContractModel):
    """Independent semantic and lexical candidate lists."""

    semantic: list[CandidateScore] = Field(description="Semantic parent matches.")
    lexical: list[CandidateScore] = Field(description="Lexical parent matches.")


class HybridSearchRepository:
    """Runs both retrieval halves with metadata predicates inside SQL."""

    def __init__(self, database: Database, candidate_pool_size: int) -> None:
        self._database = database
        self._limit = candidate_pool_size

    async def health_candidates(
        self,
        query: str,
        query_embedding: list[float] | None,
        *,
        cat_id: UUID,
        body_systems: list[BodySystem],
        urgency_tiers: list[UrgencyTier],
    ) -> HybridCandidates:
        vector = None if query_embedding is None else vector_literal(query_embedding)
        semantic, lexical = await asyncio.gather(
            self._database.fetch_all(
                _HEALTH_SEMANTIC_SQL,
                (
                    vector,
                    cat_id,
                    [value.value for value in body_systems],
                    [value.value for value in body_systems],
                    [value.value for value in urgency_tiers],
                    [value.value for value in urgency_tiers],
                    self._limit,
                ),
            )
            if vector is not None
            else _empty_rows(),
            self._database.fetch_all(
                _HEALTH_LEXICAL_SQL,
                (
                    query,
                    cat_id,
                    [value.value for value in body_systems],
                    [value.value for value in body_systems],
                    [value.value for value in urgency_tiers],
                    [value.value for value in urgency_tiers],
                    query,
                    self._limit,
                ),
            ),
        )
        return HybridCandidates(
            semantic=_candidate_scores(semantic),
            lexical=_candidate_scores(lexical),
        )

    async def behavior_candidates(
        self,
        query: str,
        query_embedding: list[float] | None,
        *,
        cat_id: UUID,
        category: BehaviorCategory | None,
        confidence: ConfidenceLevel | None,
    ) -> HybridCandidates:
        vector = None if query_embedding is None else vector_literal(query_embedding)
        semantic, lexical = await asyncio.gather(
            self._database.fetch_all(
                _BEHAVIOR_SEMANTIC_SQL,
                (
                    vector,
                    cat_id,
                    None if category is None else category.value,
                    None if category is None else category.value,
                    None if confidence is None else confidence.value,
                    None if confidence is None else confidence.value,
                    self._limit,
                ),
            )
            if vector is not None
            else _empty_rows(),
            self._database.fetch_all(
                _BEHAVIOR_LEXICAL_SQL,
                (
                    query,
                    cat_id,
                    None if category is None else category.value,
                    None if category is None else category.value,
                    None if confidence is None else confidence.value,
                    None if confidence is None else confidence.value,
                    query,
                    self._limit,
                ),
            ),
        )
        return HybridCandidates(
            semantic=_candidate_scores(semantic),
            lexical=_candidate_scores(lexical),
        )

    async def fetch_health(
        self, entry_ids: Sequence[str], *, cat_id: UUID
    ) -> list[HealthEntry]:
        if not entry_ids:
            return []
        rows = await self._database.fetch_all(
            """
            SELECT * FROM health_entries
            WHERE id = ANY(%s)
              AND EXISTS (SELECT 1 FROM cat_profiles WHERE id = %s)
            """,
            (list(entry_ids), cat_id),
        )
        by_id = {row["id"]: _health_entry(row) for row in rows}
        return [by_id[entry_id] for entry_id in entry_ids if entry_id in by_id]

    async def fetch_behavior(
        self, entry_ids: Sequence[str], *, cat_id: UUID
    ) -> list[BehaviorEntry]:
        if not entry_ids:
            return []
        rows = await self._database.fetch_all(
            """
            SELECT * FROM behavior_entries
            WHERE id = ANY(%s)
              AND EXISTS (SELECT 1 FROM cat_profiles WHERE id = %s)
            """,
            (list(entry_ids), cat_id),
        )
        by_id = {row["id"]: _behavior_entry(row) for row in rows}
        return [by_id[entry_id] for entry_id in entry_ids if entry_id in by_id]


async def _empty_rows() -> list[dict[str, object]]:
    return []


def _candidate_scores(rows: list[Row]) -> list[CandidateScore]:
    return [
        CandidateScore(
            entry_id=str(row["entry_id"]),
            score=max(0.0, float(row["score"])),
        )
        for row in rows
    ]


def _health_entry(row: Mapping[str, Any]) -> HealthEntry:
    return HealthEntry.model_validate(
        {
            key: row[key]
            for key in (
                "id",
                "topic",
                "body_system",
                "aliases",
                "keywords",
                "summary",
                "urgency_tier",
                "red_flags",
                "when_to_see_vet",
                "clarifying_questions",
                "related_topics",
                "related_conditions",
                "sources",
            )
        }
    )


def _behavior_entry(row: Mapping[str, Any]) -> BehaviorEntry:
    return BehaviorEntry.model_validate(
        {
            key: row[key]
            for key in (
                "id",
                "topic",
                "category",
                "aliases",
                "keywords",
                "summary",
                "confidence",
                "medical_flag",
                "clarifying_questions",
                "related_topics",
                "sources",
            )
        }
    )


_HEALTH_SEMANTIC_SQL = """
SELECT
    c.parent_entry_id AS entry_id,
    MAX(GREATEST(0, 1 - (c.embedding <=> %s::vector))) AS score
FROM chunks AS c
JOIN corpus_entries AS registry
  ON registry.id = c.parent_entry_id AND registry.kind = 'health'
JOIN health_entries AS parent ON parent.id = c.parent_entry_id
WHERE c.embedding IS NOT NULL
  AND EXISTS (SELECT 1 FROM cat_profiles WHERE id = %s)
  AND (cardinality(%s::text[]) = 0 OR parent.body_system::text = ANY(%s::text[]))
  AND (cardinality(%s::text[]) = 0 OR parent.urgency_tier::text = ANY(%s::text[]))
GROUP BY c.parent_entry_id
ORDER BY score DESC
LIMIT %s
"""

_HEALTH_LEXICAL_SQL = """
SELECT
    id AS entry_id,
    GREATEST(0, ts_rank_cd(search_vector, websearch_to_tsquery('english', %s))) AS score
FROM health_entries
WHERE EXISTS (SELECT 1 FROM cat_profiles WHERE id = %s)
  AND (cardinality(%s::text[]) = 0 OR body_system::text = ANY(%s::text[]))
  AND (cardinality(%s::text[]) = 0 OR urgency_tier::text = ANY(%s::text[]))
  AND search_vector @@ websearch_to_tsquery('english', %s)
ORDER BY score DESC
LIMIT %s
"""

_BEHAVIOR_SEMANTIC_SQL = """
SELECT
    c.parent_entry_id AS entry_id,
    MAX(GREATEST(0, 1 - (c.embedding <=> %s::vector))) AS score
FROM chunks AS c
JOIN corpus_entries AS registry
  ON registry.id = c.parent_entry_id AND registry.kind = 'behavior'
JOIN behavior_entries AS parent ON parent.id = c.parent_entry_id
WHERE c.embedding IS NOT NULL
  AND EXISTS (SELECT 1 FROM cat_profiles WHERE id = %s)
  AND (%s::text IS NULL OR parent.category::text = %s::text)
  AND (%s::text IS NULL OR parent.confidence::text = %s::text)
GROUP BY c.parent_entry_id
ORDER BY score DESC
LIMIT %s
"""

_BEHAVIOR_LEXICAL_SQL = """
SELECT
    id AS entry_id,
    GREATEST(0, ts_rank_cd(search_vector, websearch_to_tsquery('english', %s))) AS score
FROM behavior_entries
WHERE EXISTS (SELECT 1 FROM cat_profiles WHERE id = %s)
  AND (%s::text IS NULL OR category::text = %s::text)
  AND (%s::text IS NULL OR confidence::text = %s::text)
  AND search_vector @@ websearch_to_tsquery('english', %s)
ORDER BY score DESC
LIMIT %s
"""
