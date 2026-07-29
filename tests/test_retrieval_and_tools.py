"""Retrieval disposal and typed tool failure regressions."""

from uuid import uuid4

from app.corpus_paths import resolve_corpus_dir
from app.ingestion.csv_loader import load_health
from app.ingestion.embeddings import DeterministicEmbeddingProvider
from app.orchestration.health import _dispose_health_draft
from app.retrieval.knowledge import PostgresVetKnowledgeRetriever
from app.retrieval.repository import CandidateScore, HybridCandidates
from app.retrieval.rerank import TokenOverlapReranker
from app.schemas.enums import ToolErrorCode, TriageResponseKind, UrgencyTier
from app.schemas.llm import Claim, TriageResult
from app.tools.contracts import (
    AccountLookupInput,
    FunFactFetcherInput,
    RankedHealthEntry,
    RetrievalScores,
    VetKnowledgeRetrieverInput,
)
from app.tools.implementations import PostgresFunFactFetcher, PostgresProfileStore


HEALTH = load_health(resolve_corpus_dir() / "MASTER_health_corpus.csv")


class CandidateRepository:
    async def health_candidates(self, *args: object, **kwargs: object) -> HybridCandidates:
        return HybridCandidates(
            semantic=[CandidateScore(entry_id=HEALTH[0].id, score=0.8)],
            lexical=[],
        )

    async def fetch_health(
        self, entry_ids: list[str], *, cat_id: object
    ) -> list[object]:
        return [HEALTH[0]]


class FailingDatabase:
    async def fetch_one(self, query: str, params: object = ()) -> None:
        raise RuntimeError("database offline")

    async def fetch_all(self, query: str, params: object = ()) -> list[object]:
        raise RuntimeError("database offline")

    async def execute(self, query: str, params: object = ()) -> int:
        raise RuntimeError("database offline")


class CapturingDatabase:
    def __init__(self) -> None:
        self.params: object = None

    async def fetch_all(self, query: str, params: object = ()) -> list[object]:
        self.params = params
        return []


async def test_successful_rerank_can_reject_every_irrelevant_parent() -> None:
    retriever = PostgresVetKnowledgeRetriever(
        CandidateRepository(),
        DeterministicEmbeddingProvider(1024),
        TokenOverlapReranker(),
        minimum_rerank_score=0.05,
    )
    result = await retriever.retrieve(
        VetKnowledgeRetrieverInput(
            query="xyzzy quux frobnicate",
            cat_id=uuid4(),
            filters={},
        )
    )
    assert result.entries == []
    assert result.error is None


def test_only_claimed_entries_influence_severity_and_citations() -> None:
    claimed = next(
        entry for entry in HEALTH if entry.urgency_tier is UrgencyTier.MONITOR
    )
    unrelated = next(
        entry for entry in HEALTH if entry.urgency_tier is UrgencyTier.EMERGENCY
    )
    ranked = [
        RankedHealthEntry(
            entry_id=entry.id,
            scores=RetrievalScores(lexical=1, semantic=1, rerank=1),
            entry=entry,
        )
        for entry in (claimed, unrelated)
    ]
    draft = TriageResult(
        severity=UrgencyTier.ROUTINE,
        claims=[
            Claim(
                text="Supported claim",
                source_entry_id=claimed.id,
                source_url=None,
            )
        ],
        message="Supported claim",
        retrieved_entry_ids=[claimed.id, unrelated.id],
        response_kind=TriageResponseKind.TRIAGE,
    )
    result = _dispose_health_draft(draft, ranked)
    assert result.severity is UrgencyTier.MONITOR
    assert f"[{claimed.id}]" in result.message
    assert f"[{unrelated.id}]" not in result.message


async def test_profile_store_and_fact_fetcher_return_typed_failures() -> None:
    profile = await PostgresProfileStore(FailingDatabase()).get_account(
        AccountLookupInput(account_id=uuid4())
    )
    assert profile.error is not None
    assert profile.error.code is ToolErrorCode.UNAVAILABLE

    facts = await PostgresFunFactFetcher(FailingDatabase()).fetch(
        FunFactFetcherInput(
            cat_id=uuid4(), active_cat_tags=[], exclude_ids=[]
        )
    )
    assert facts.error is not None
    assert facts.error.code is ToolErrorCode.UNAVAILABLE


async def test_fact_fetcher_carries_required_cat_scope_into_sql() -> None:
    database = CapturingDatabase()
    cat_id = uuid4()
    result = await PostgresFunFactFetcher(database).fetch(
        FunFactFetcherInput(
            cat_id=cat_id,
            active_cat_tags=["breed:bengal"],
            exclude_ids=[],
        )
    )
    assert result.error is None
    assert database.params[0] == cat_id
    assert "all-cats" in database.params[1]
