"""Phase 1 retriever interface implementations."""

import logging
import re
from collections import defaultdict
from dataclasses import dataclass

from app.ingestion.embeddings import EmbeddingProvider
from app.retrieval.repository import HybridCandidates, HybridSearchRepository
from app.retrieval.rerank import RerankResult, Reranker
from app.schemas.corpora import BehaviorEntry, HealthEntry
from app.schemas.enums import BodySystem, ToolErrorCode
from app.tools.contracts import (
    BehaviorKnowledgeRetriever,
    BehaviorKnowledgeRetrieverInput,
    BehaviorKnowledgeRetrieverOutput,
    RankedBehaviorEntry,
    RankedHealthEntry,
    RetrievalScores,
    ToolError,
    VetKnowledgeRetriever,
    VetKnowledgeRetrieverInput,
    VetKnowledgeRetrieverOutput,
)

logger = logging.getLogger(__name__)
RRF_K = 60


@dataclass(frozen=True)
class _AcceptedRerankItem:
    index: int
    score: float | None


class PostgresVetKnowledgeRetriever(VetKnowledgeRetriever):
    """Hybrid child-match/parent-return implementation for medical facts."""

    def __init__(
        self,
        repository: HybridSearchRepository,
        embedder: EmbeddingProvider,
        reranker: Reranker,
        *,
        rerank_pool_size: int = 20,
        final_size: int = 5,
        minimum_rerank_score: float = 0.05,
    ) -> None:
        self._repository = repository
        self._embedder = embedder
        self._reranker = reranker
        self._rerank_pool_size = rerank_pool_size
        self._final_size = final_size
        self._minimum_rerank_score = minimum_rerank_score

    async def retrieve(
        self, request: VetKnowledgeRetrieverInput
    ) -> VetKnowledgeRetrieverOutput:
        try:
            systems = request.filters.body_systems or infer_body_systems(request.query)
            embedding = await self._embedder.embed_query(request.query)
            candidates = await self._repository.health_candidates(
                request.query,
                embedding,
                cat_id=request.cat_id,
                body_systems=systems,
                urgency_tiers=request.filters.urgency_tiers,
            )
            fused_ids, scores = _rrf(candidates, self._rerank_pool_size)
            parents = await self._repository.fetch_health(
                fused_ids, cat_id=request.cat_id
            )
            ranked, rerank_error = await _rerank_health(
                request.query,
                parents,
                scores,
                self._reranker,
                self._final_size,
                self._minimum_rerank_score,
            )
            return VetKnowledgeRetrieverOutput(entries=ranked, error=rerank_error)
        except Exception as exc:  # repository/provider exceptions fail at tool boundary
            logger.exception("veterinary retrieval failed closed")
            return VetKnowledgeRetrieverOutput(
                entries=[],
                error=_tool_error(str(exc)),
            )


class PostgresBehaviorKnowledgeRetriever(BehaviorKnowledgeRetriever):
    """Hybrid parent retrieval that preserves candidates for evidence selection.

    Behavior reranker magnitudes are not calibrated relevance probabilities, so
    this layer ranks candidates but does not discard them by absolute score.
    """

    def __init__(
        self,
        repository: HybridSearchRepository,
        embedder: EmbeddingProvider,
        reranker: Reranker,
        *,
        rerank_pool_size: int = 20,
        final_size: int = 5,
    ) -> None:
        self._repository = repository
        self._embedder = embedder
        self._reranker = reranker
        self._rerank_pool_size = rerank_pool_size
        self._final_size = final_size

    async def retrieve(
        self, request: BehaviorKnowledgeRetrieverInput
    ) -> BehaviorKnowledgeRetrieverOutput:
        try:
            embedding = await self._embedder.embed_query(request.query)
            candidates = await self._repository.behavior_candidates(
                request.query,
                embedding,
                cat_id=request.cat_id,
                category=request.filters.category,
                confidence=request.filters.confidence,
            )
            fused_ids, scores = _rrf(candidates, self._rerank_pool_size)
            parents = await self._repository.fetch_behavior(
                fused_ids, cat_id=request.cat_id
            )
            ranked, rerank_error = await _rerank_behavior(
                request.query,
                parents,
                scores,
                self._reranker,
                self._final_size,
            )
            return BehaviorKnowledgeRetrieverOutput(
                entries=ranked, error=rerank_error
            )
        except Exception as exc:
            logger.exception("behavior retrieval failed closed")
            return BehaviorKnowledgeRetrieverOutput(
                entries=[],
                error=_tool_error(str(exc)),
            )


def infer_body_systems(query: str) -> list[BodySystem]:
    """Conservative deterministic metadata inference used as a hard SQL filter."""
    text = query.casefold()
    mappings = {
        BodySystem.URINARY: ("pee", "urine", "urinary", "litter box", "flutd"),
        BodySystem.RESPIRATORY: ("breath", "cough", "wheez", "asthma"),
        BodySystem.DIGESTIVE: ("vomit", "throwing up", "diarrhea", "stool", "poop"),
        BodySystem.EYES: ("eye", "squint", "pupil"),
        BodySystem.EARS: ("ear",),
        BodySystem.SKIN: ("skin", "itch", "fur", "hair loss"),
        BodySystem.DENTAL: ("tooth", "teeth", "mouth", "gum", "breath"),
        BodySystem.KIDNEY: ("kidney", "drinking", "thirst"),
        BodySystem.NEUROLOGICAL: ("seizure", "collapse", "unresponsive"),
        BodySystem.TOXIN: ("toxin", "poison", "lily", "medication", "pill"),
        BodySystem.MUSCULOSKELETAL: ("limp", "leg", "weight", "jump"),
    }
    matches = [
        system
        for system, terms in mappings.items()
        if any(re.search(rf"\b{re.escape(term)}", text) for term in terms)
    ]
    return matches[:1] if len(matches) == 1 else []


def _rrf(
    candidates: HybridCandidates, limit: int
) -> tuple[list[str], dict[str, tuple[float, float]]]:
    fused: dict[str, float] = defaultdict(float)
    semantic_scores = {item.entry_id: item.score for item in candidates.semantic}
    lexical_scores = {item.entry_id: item.score for item in candidates.lexical}
    for ranked in (candidates.semantic, candidates.lexical):
        for rank, item in enumerate(ranked, start=1):
            fused[item.entry_id] += 1 / (RRF_K + rank)
    ordered = sorted(fused, key=lambda entry_id: (-fused[entry_id], entry_id))[:limit]
    return ordered, {
        entry_id: (
            semantic_scores.get(entry_id, 0.0),
            lexical_scores.get(entry_id, 0.0),
        )
        for entry_id in ordered
    }


async def _rerank_health(
    query: str,
    entries: list[HealthEntry],
    scores: dict[str, tuple[float, float]],
    reranker: Reranker,
    final_size: int,
    minimum_rerank_score: float,
) -> tuple[list[RankedHealthEntry], ToolError | None]:
    result = await reranker.rerank(
        query, [_health_document(entry) for entry in entries], final_size
    )
    order = _accepted_rerank_items(
        result, len(entries), final_size, minimum_rerank_score
    )
    ranked = []
    for item in order:
        entry = entries[item.index]
        semantic, lexical = scores[entry.id]
        ranked.append(
            RankedHealthEntry(
                entry_id=entry.id,
                scores=RetrievalScores(
                    lexical=lexical,
                    semantic=semantic,
                    rerank=None if item.score is None else max(0.0, item.score),
                ),
                entry=entry,
            )
        )
    return ranked, result.error


async def _rerank_behavior(
    query: str,
    entries: list[BehaviorEntry],
    scores: dict[str, tuple[float, float]],
    reranker: Reranker,
    final_size: int,
) -> tuple[list[RankedBehaviorEntry], ToolError | None]:
    result = await reranker.rerank(
        query, [_behavior_document(entry) for entry in entries], final_size
    )
    # Do not threshold behavior candidates by raw reranker magnitude. The local
    # MS-MARCO model produces saturated sigmoid scores (valid paraphrases can be
    # near zero); orchestration selects sourced mode from rank agreement and
    # deterministic coverage instead.
    order = _accepted_rerank_items(result, len(entries), final_size, 0.0)
    ranked = []
    for item in order:
        entry = entries[item.index]
        semantic, lexical = scores[entry.id]
        ranked.append(
            RankedBehaviorEntry(
                entry_id=entry.id,
                scores=RetrievalScores(
                    lexical=lexical,
                    semantic=semantic,
                    rerank=None if item.score is None else max(0.0, item.score),
                ),
                entry=entry,
            )
        )
    return ranked, result.error


def _health_document(entry: HealthEntry) -> str:
    return " ".join([entry.topic, entry.summary, *entry.aliases, *entry.keywords])


def _behavior_document(entry: BehaviorEntry) -> str:
    return " ".join([entry.topic, entry.summary, *entry.aliases, *entry.keywords])


def _accepted_rerank_items(
    result: RerankResult,
    entry_count: int,
    final_size: int,
    minimum_score: float,
) -> list[_AcceptedRerankItem]:
    if result.items:
        return [
            _AcceptedRerankItem(index=item.index, score=item.score)
            for item in result.items
            if item.score >= minimum_score
        ][:final_size]
    if result.error is not None:
        return [
            _AcceptedRerankItem(index=index, score=None)
            for index in range(min(final_size, entry_count))
        ]
    return []


def _tool_error(message: str) -> ToolError:
    return ToolError(
        code=ToolErrorCode.UNAVAILABLE,
        message=message or "retrieval unavailable",
        retryable=True,
    )
