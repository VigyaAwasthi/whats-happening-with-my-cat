"""Application composition root for production and zero-cost development."""

from dataclasses import dataclass
from uuid import UUID

from app.corpus_paths import resolve_corpus_dir
from app.db import PostgresDatabase
from app.ingestion.csv_loader import load_behavior, load_health
from app.ingestion.embeddings import (
    DeterministicEmbeddingProvider,
    VoyageEmbeddingProvider,
)
from app.llm.adapters import FastMemorySummarizer
from app.llm.client import (
    AnthropicStructuredClient,
    DevelopmentStructuredClient,
    HttpAnthropicTransport,
    PostgresSpendLedger,
    SpendTracker,
    TokenPricing,
)
from app.memory.repository import InMemoryMemoryRepository, PostgresMemoryRepository
from app.observability.repository import (
    InMemoryTraceRepository,
    PostgresTraceRepository,
    TraceRepository,
)
from app.memory.service import CatMemoryService, PostgresMemoryRetriever
from app.orchestration.behavior import BehaviorOrchestrator
from app.orchestration.health import HealthOrchestrator
from app.repositories.application import (
    ApplicationRepository,
    AuthService,
    DevelopmentAuthService,
    InMemoryApplicationRepository,
    PostgresApplicationRepository,
    SupabaseAuthService,
    development_account,
)
from app.retrieval.development import (
    InMemoryBehaviorKnowledgeRetriever,
    InMemoryVetKnowledgeRetriever,
)
from app.retrieval.knowledge import (
    PostgresBehaviorKnowledgeRetriever,
    PostgresVetKnowledgeRetriever,
)
from app.retrieval.repository import HybridSearchRepository
from app.retrieval.rerank import (
    HostedAPIReranker,
    LocalCrossEncoderReranker,
)
from app.runtime_config import RerankerMode, RuntimeMode, RuntimeSettings
from app.safety.groundedness import CompositeGroundednessValidator
from app.safety.red_flags import DeterministicRedFlagChecker


DEVELOPMENT_ACCOUNT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


@dataclass
class ApplicationServices:
    """Objects used by route dependencies and application lifespan."""

    settings: RuntimeSettings
    auth: AuthService
    repository: ApplicationRepository
    health: HealthOrchestrator
    behavior: BehaviorOrchestrator
    memory_repository: object
    traces: TraceRepository | None = None
    database: PostgresDatabase | None = None


_services: ApplicationServices | None = None


async def build_services(settings: RuntimeSettings) -> ApplicationServices:
    """Build one explicit object graph with no agent framework."""
    if settings.runtime_mode is RuntimeMode.DEVELOPMENT:
        return _build_development(settings)
    return await _build_production(settings)


def get_services() -> ApplicationServices:
    """Return configured services; app lifespan must initialize first."""
    if _services is None:
        raise RuntimeError("application services are not initialized")
    return _services


def set_services(services: ApplicationServices | None) -> None:
    """Set or clear the composition root, including from tests."""
    global _services
    _services = services


async def close_services() -> None:
    """Close production resources and clear the composition root."""
    global _services
    if _services is not None and _services.database is not None:
        await _services.database.close()
    _services = None


def _build_development(settings: RuntimeSettings) -> ApplicationServices:
    corpus_dir = resolve_corpus_dir(settings.corpus_source_dir)
    traces = InMemoryTraceRepository()
    repository = InMemoryApplicationRepository(
        development_account(DEVELOPMENT_ACCOUNT_ID), traces
    )
    repository.load_facts(corpus_dir / "MASTER_fun_facts.csv")
    memory_repository = InMemoryMemoryRepository()
    embedder = DeterministicEmbeddingProvider(settings.embedding_dimensions)
    llm = DevelopmentStructuredClient()
    summarizer = FastMemorySummarizer(llm, settings.anthropic_fast_model)
    memory_writer = CatMemoryService(
        memory_repository,
        embedder,
        summarizer,
        summary_message_limit=settings.memory_summary_message_limit,
    )
    memory_retriever = PostgresMemoryRetriever(memory_repository, embedder)
    red_flags = DeterministicRedFlagChecker()
    groundedness = CompositeGroundednessValidator(
        llm, settings.anthropic_fast_model
    )
    health = HealthOrchestrator(
        llm=llm,
        fast_model=settings.anthropic_fast_model,
        health_model=settings.anthropic_health_model,
        vet_retriever=InMemoryVetKnowledgeRetriever(
            load_health(corpus_dir / "MASTER_health_corpus.csv")
        ),
        memory_retriever=memory_retriever,
        memory_writer=memory_writer,
        red_flags=red_flags,
        groundedness=groundedness,
        traces=traces,
    )
    behavior = BehaviorOrchestrator(
        llm=llm,
        fast_model=settings.anthropic_fast_model,
        behavior_model=settings.anthropic_behavior_model,
        health_signal_threshold=settings.health_signal_threshold,
        health_signal_medium_threshold=settings.health_signal_medium_threshold,
        behavior_grounding_min_query_coverage=(
            settings.behavior_grounding_min_query_coverage
        ),
        behavior_grounding_min_query_terms=(
            settings.behavior_grounding_min_query_terms
        ),
        behavior_retriever=InMemoryBehaviorKnowledgeRetriever(
            load_behavior(corpus_dir / "MASTER_behavior_corpus.csv")
        ),
        memory_retriever=memory_retriever,
        memory_writer=memory_writer,
        red_flags=red_flags,
        groundedness=groundedness,
        traces=traces,
    )
    return ApplicationServices(
        settings=settings,
        auth=DevelopmentAuthService(DEVELOPMENT_ACCOUNT_ID),
        repository=repository,
        health=health,
        behavior=behavior,
        memory_repository=memory_repository,
        traces=traces,
    )


async def _build_production(settings: RuntimeSettings) -> ApplicationServices:
    database = PostgresDatabase(settings.database_url.get_secret_value())
    await database.open()
    traces = PostgresTraceRepository(database)
    embedder = VoyageEmbeddingProvider(
        api_key=settings.voyage_api_key.get_secret_value(),
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )
    reranker = (
        HostedAPIReranker(
            url=settings.reranker_url,
            api_key=settings.reranker_api_key.get_secret_value(),
            model=settings.reranker_model,
        )
        if settings.reranker_mode is RerankerMode.HOSTED
        else LocalCrossEncoderReranker(settings.reranker_model)
    )
    llm = AnthropicStructuredClient(
        HttpAnthropicTransport(settings.anthropic_api_key.get_secret_value()),
        SpendTracker(
            cap_usd=settings.hard_spend_cap_usd,
            pricing=_model_pricing(settings),
            ledger=PostgresSpendLedger(database),
            window=settings.spend_window.value,
            warning_ratio=settings.spend_warning_ratio,
        ),
    )
    memory_repository = PostgresMemoryRepository(database)
    summarizer = FastMemorySummarizer(llm, settings.anthropic_fast_model)
    memory_writer = CatMemoryService(
        memory_repository,
        embedder,
        summarizer,
        summary_message_limit=settings.memory_summary_message_limit,
    )
    memory_retriever = PostgresMemoryRetriever(memory_repository, embedder)
    hybrid = HybridSearchRepository(
        database, settings.retrieval_candidate_pool_size
    )
    red_flags = DeterministicRedFlagChecker()
    groundedness = CompositeGroundednessValidator(
        llm, settings.anthropic_fast_model
    )
    health = HealthOrchestrator(
        llm=llm,
        fast_model=settings.anthropic_fast_model,
        health_model=settings.anthropic_health_model,
        vet_retriever=PostgresVetKnowledgeRetriever(
            hybrid,
            embedder,
            reranker,
            rerank_pool_size=settings.retrieval_rerank_output_size,
            final_size=settings.retrieval_final_context_size,
            minimum_rerank_score=settings.retrieval_min_rerank_score,
        ),
        memory_retriever=memory_retriever,
        memory_writer=memory_writer,
        red_flags=red_flags,
        groundedness=groundedness,
        traces=traces,
    )
    behavior = BehaviorOrchestrator(
        llm=llm,
        fast_model=settings.anthropic_fast_model,
        behavior_model=settings.anthropic_behavior_model,
        health_signal_threshold=settings.health_signal_threshold,
        health_signal_medium_threshold=settings.health_signal_medium_threshold,
        behavior_grounding_min_query_coverage=(
            settings.behavior_grounding_min_query_coverage
        ),
        behavior_grounding_min_query_terms=(
            settings.behavior_grounding_min_query_terms
        ),
        behavior_retriever=PostgresBehaviorKnowledgeRetriever(
            hybrid,
            embedder,
            reranker,
            rerank_pool_size=settings.retrieval_rerank_output_size,
            final_size=settings.retrieval_final_context_size,
        ),
        memory_retriever=memory_retriever,
        memory_writer=memory_writer,
        red_flags=red_flags,
        groundedness=groundedness,
        traces=traces,
    )
    repository = PostgresApplicationRepository(database, traces)
    return ApplicationServices(
        settings=settings,
        auth=SupabaseAuthService(
            supabase_url=settings.supabase_url,
            anon_key=settings.supabase_anon_key.get_secret_value(),
            service_role_key=settings.supabase_service_role_key.get_secret_value(),
            database=database,
            email_redirect_url=settings.supabase_email_redirect_url,
        ),
        repository=repository,
        health=health,
        behavior=behavior,
        memory_repository=memory_repository,
        traces=traces,
        database=database,
    )


def _model_pricing(settings: RuntimeSettings) -> dict[str, TokenPricing]:
    """Map configured model ids to their independently configured billing rates."""
    configured = [
        (
            settings.anthropic_fast_model,
            TokenPricing(
                input_per_million_usd=settings.anthropic_fast_input_cost_per_million_usd,
                output_per_million_usd=settings.anthropic_fast_output_cost_per_million_usd,
                cache_write_per_million_usd=(
                    settings.anthropic_fast_cache_write_cost_per_million_usd
                ),
                cache_read_per_million_usd=(
                    settings.anthropic_fast_cache_read_cost_per_million_usd
                ),
            ),
        ),
        (
            settings.anthropic_behavior_model,
            TokenPricing(
                input_per_million_usd=(
                    settings.anthropic_behavior_input_cost_per_million_usd
                ),
                output_per_million_usd=(
                    settings.anthropic_behavior_output_cost_per_million_usd
                ),
                cache_write_per_million_usd=(
                    settings.anthropic_behavior_cache_write_cost_per_million_usd
                ),
                cache_read_per_million_usd=(
                    settings.anthropic_behavior_cache_read_cost_per_million_usd
                ),
            ),
        ),
        (
            settings.anthropic_health_model,
            TokenPricing(
                input_per_million_usd=(
                    settings.anthropic_health_input_cost_per_million_usd
                ),
                output_per_million_usd=(
                    settings.anthropic_health_output_cost_per_million_usd
                ),
                cache_write_per_million_usd=(
                    settings.anthropic_health_cache_write_cost_per_million_usd
                ),
                cache_read_per_million_usd=(
                    settings.anthropic_health_cache_read_cost_per_million_usd
                ),
            ),
        ),
    ]
    pricing: dict[str, TokenPricing] = {}
    for model, rates in configured:
        previous = pricing.get(model)
        if previous is not None and previous != rates:
            raise ValueError(
                f"model {model!r} has conflicting prices across configured tiers"
            )
        pricing[model] = rates
    return pricing
