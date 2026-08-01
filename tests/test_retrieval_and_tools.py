"""Retrieval disposal and typed tool failure regressions."""

from uuid import uuid4

from app.corpus_paths import resolve_corpus_dir
from app.ingestion.csv_loader import load_behavior, load_health
from app.ingestion.embeddings import DeterministicEmbeddingProvider
from app.orchestration.health import _dispose_health_draft, _format_health_source
from app.retrieval.knowledge import (
    PostgresVetKnowledgeRetriever,
    _rerank_behavior,
    infer_body_systems,
)
from app.retrieval.repository import CandidateScore, HybridCandidates
from app.retrieval.rerank import RerankItem, RerankResult, TokenOverlapReranker
from app.schemas.enums import BodySystem, ToolErrorCode, TriageResponseKind, UrgencyTier
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
BEHAVIOR = load_behavior(resolve_corpus_dir() / "MASTER_behavior_corpus.csv")


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


class LowScoreReranker:
    """Return a valid winner whose uncalibrated magnitude is near zero."""

    async def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> RerankResult:
        return RerankResult(items=[RerankItem(index=0, score=0.0015)])


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


async def test_behavior_rerank_preserves_valid_low_score_winner() -> None:
    entry = next(item for item in BEHAVIOR if item.id == "kneading")
    ranked, error = await _rerank_behavior(
        "she only kneads on one blanket",
        [entry],
        {entry.id: (0.8, 0.0)},
        LowScoreReranker(),
        final_size=5,
    )
    assert error is None
    assert [item.entry_id for item in ranked] == ["kneading"]
    assert ranked[0].scores.rerank == 0.0015


def test_body_system_inference_does_not_find_itch_inside_twitching() -> None:
    assert infer_body_systems("his right eye is twitching") == [BodySystem.EYES]


def test_assessed_severity_is_not_inflated_by_retrieved_entries() -> None:
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
        message=(
            "Supported claim\n\nSources:\n"
            f"[{claimed.id}] Example — Vet: https://example.test/source"
        ),
        retrieved_entry_ids=[claimed.id, unrelated.id],
        response_kind=TriageResponseKind.TRIAGE,
    )
    result = _dispose_health_draft(draft, ranked)
    assert result.severity is UrgencyTier.ROUTINE
    assert "Sources:" not in result.message
    assert claimed.id not in result.message
    assert unrelated.id not in result.message
    assert "https://" not in result.message
    assert result.claims[0].source_title == claimed.sources[0].title
    assert result.claims[0].source_organization == claimed.sources[0].organization
    assert result.claims[0].source_url == claimed.sources[0].url


def test_health_source_contract_distinguishes_linked_and_unlinked_sources() -> None:
    claim = Claim(
        text="Grounded claim",
        source_entry_id="entry",
        source_title="Readable title",
        source_organization="Trusted organization",
        source_url=None,
    )
    assert claim.source_title == "Readable title"
    assert claim.source_url is None
    assert (
        _format_health_source(
            "entry", "Readable title", "Trusted organization", None
        )
        == "[entry] Readable title — Trusted organization"
    )
    assert _format_health_source(
        "entry",
        "Readable title",
        "Trusted organization",
        "https://example.test/source",
    ).endswith(": https://example.test/source")
    assert _format_health_source(
        "entry",
        "Readable title",
        "Trusted organization",
        "https://example.test/source [VERIFY exact subpage]",
    ) == "[entry] Readable title — Trusted organization"


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
