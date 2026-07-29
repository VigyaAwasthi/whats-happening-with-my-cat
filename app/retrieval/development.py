"""Seeded deterministic corpus retrieval for zero-cost development."""

import re
from collections.abc import Sequence
from typing import TypeVar

from app.schemas.corpora import BehaviorEntry, CorporaEntryBase, HealthEntry
from app.tools.contracts import (
    BehaviorKnowledgeRetriever,
    BehaviorKnowledgeRetrieverInput,
    BehaviorKnowledgeRetrieverOutput,
    RankedBehaviorEntry,
    RankedHealthEntry,
    RetrievalScores,
    VetKnowledgeRetriever,
    VetKnowledgeRetrieverInput,
    VetKnowledgeRetrieverOutput,
)

CorpusEntryT = TypeVar("CorpusEntryT", bound=CorporaEntryBase)


class InMemoryVetKnowledgeRetriever(VetKnowledgeRetriever):
    """Token-overlap development retriever over validated health parents."""

    def __init__(self, entries: list[HealthEntry], final_size: int = 5) -> None:
        self._entries = entries
        self._limit = final_size

    async def retrieve(
        self, request: VetKnowledgeRetrieverInput
    ) -> VetKnowledgeRetrieverOutput:
        filtered = [
            entry
            for entry in self._entries
            if (
                not request.filters.body_systems
                or entry.body_system in request.filters.body_systems
            )
            and (
                not request.filters.urgency_tiers
                or entry.urgency_tier in request.filters.urgency_tiers
            )
        ]
        ranked = _rank(request.query, filtered)
        return VetKnowledgeRetrieverOutput(
            entries=[
                RankedHealthEntry(
                    entry_id=entry.id,
                    scores=RetrievalScores(
                        lexical=score, semantic=score, rerank=score
                    ),
                    entry=entry,
                )
                for score, entry in ranked[: self._limit]
            ]
        )


class InMemoryBehaviorKnowledgeRetriever(BehaviorKnowledgeRetriever):
    """Token-overlap development retriever over validated behavior parents."""

    def __init__(self, entries: list[BehaviorEntry], final_size: int = 5) -> None:
        self._entries = entries
        self._limit = final_size

    async def retrieve(
        self, request: BehaviorKnowledgeRetrieverInput
    ) -> BehaviorKnowledgeRetrieverOutput:
        filtered = [
            entry
            for entry in self._entries
            if (
                request.filters.category is None
                or entry.category is request.filters.category
            )
            and (
                request.filters.confidence is None
                or entry.confidence is request.filters.confidence
            )
        ]
        ranked = _rank(request.query, filtered)
        return BehaviorKnowledgeRetrieverOutput(
            entries=[
                RankedBehaviorEntry(
                    entry_id=entry.id,
                    scores=RetrievalScores(
                        lexical=score, semantic=score, rerank=score
                    ),
                    entry=entry,
                )
                for score, entry in ranked[: self._limit]
            ]
        )


def _rank(
    query: str, entries: Sequence[CorpusEntryT]
) -> list[tuple[float, CorpusEntryT]]:
    query_terms = _terms(query)
    ranked: list[tuple[float, CorpusEntryT]] = []
    for entry in entries:
        text = " ".join(
            [entry.topic, entry.summary, *entry.aliases, *entry.keywords]
        )
        overlap = query_terms & _terms(text)
        if overlap:
            ranked.append((float(len(overlap)), entry))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].id))
    return ranked


def _terms(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.casefold()))
