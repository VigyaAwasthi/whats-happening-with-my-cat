"""Phase 2 runtime settings layered over the fixed Phase 1 settings contract."""

import os
from decimal import Decimal
from enum import Enum
from pathlib import Path

from pydantic import Field, PositiveInt, SecretStr, model_validator

from app.config import Settings
from app.corpus_paths import PROMPT_CORPUS_DIR


class RuntimeMode(str, Enum):
    """Runtime backing mode."""

    PRODUCTION = "production"
    DEVELOPMENT = "development"


class RerankerMode(str, Enum):
    """Selectable reranker implementation."""

    HOSTED = "hosted"
    LOCAL = "local"


class RuntimeSettings(Settings):
    """Implementation settings added without changing the Phase 1 contract."""

    anthropic_fast_model: str = Field(
        default="claude-haiku-4-5-20251001",
        min_length=1,
        description="Date-pinned fast model for extraction, judging, and summaries.",
    )
    anthropic_reasoning_model: str = Field(
        default="claude-sonnet-5",
        min_length=1,
        description="Configured reasoning-tier alias retained by the base contract.",
    )
    embedding_model: str = Field(
        default="voyage-3",
        min_length=1,
        description="Voyage embedding model identifier.",
    )
    embedding_dimensions: PositiveInt = Field(
        default=1024, description="Voyage vector dimension mirrored by migration 003."
    )
    voyage_api_key: SecretStr = Field(
        default=SecretStr("development-unused"),
        description="Voyage API key; unused by the deterministic development embedder.",
    )
    anthropic_behavior_model: str = Field(
        default="claude-sonnet-5",
        min_length=1,
        description="Strong model identifier for behavior interpretation.",
    )
    anthropic_health_model: str = Field(
        default="claude-sonnet-5",
        min_length=1,
        description="Strong model identifier for health triage.",
    )
    reranker_mode: RerankerMode = Field(
        default=RerankerMode.LOCAL, description="Hosted or local reranker selection."
    )
    reranker_model: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2",
        min_length=1,
        description="Hosted or local cross-encoder model identifier.",
    )
    retrieval_rerank_output_size: PositiveInt = Field(
        default=20, description="Parent candidates sent to the cross-encoder."
    )
    retrieval_final_context_size: PositiveInt = Field(
        default=5, le=5, description="Final parent entries supplied to a model."
    )
    retrieval_min_rerank_score: float = Field(
        default=0.05,
        ge=0,
        description="Minimum normalized cross-encoder score for relevant context.",
    )
    memory_summary_message_limit: PositiveInt = Field(
        default=20, description="Message threshold before rolling summarization."
    )
    memory_retrieval_limit: PositiveInt = Field(
        default=5, le=5, description="Maximum cat-isolated memories retrieved."
    )
    health_signal_threshold: float = Field(
        default=0.70, ge=0, le=1, description="Behavior-to-health nudge threshold."
    )
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
        ],
        min_length=1,
        description="Exact browser origins allowed to call the API.",
    )
    anthropic_fast_input_cost_per_million_usd: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        description="Fast-model regular input-token price.",
    )
    anthropic_fast_output_cost_per_million_usd: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        description="Fast-model output-token price.",
    )
    anthropic_fast_cache_write_cost_per_million_usd: Decimal = Field(
        default=Decimal("0"), ge=0, description="Fast-model cache-write token price."
    )
    anthropic_fast_cache_read_cost_per_million_usd: Decimal = Field(
        default=Decimal("0"), ge=0, description="Fast-model cache-read token price."
    )
    anthropic_behavior_input_cost_per_million_usd: Decimal = Field(
        default=Decimal("0"), ge=0, description="Behavior-model regular input price."
    )
    anthropic_behavior_output_cost_per_million_usd: Decimal = Field(
        default=Decimal("0"), ge=0, description="Behavior-model output price."
    )
    anthropic_behavior_cache_write_cost_per_million_usd: Decimal = Field(
        default=Decimal("0"), ge=0, description="Behavior-model cache-write price."
    )
    anthropic_behavior_cache_read_cost_per_million_usd: Decimal = Field(
        default=Decimal("0"), ge=0, description="Behavior-model cache-read price."
    )
    anthropic_health_input_cost_per_million_usd: Decimal = Field(
        default=Decimal("0"), ge=0, description="Health-model regular input price."
    )
    anthropic_health_output_cost_per_million_usd: Decimal = Field(
        default=Decimal("0"), ge=0, description="Health-model output price."
    )
    anthropic_health_cache_write_cost_per_million_usd: Decimal = Field(
        default=Decimal("0"), ge=0, description="Health-model cache-write price."
    )
    anthropic_health_cache_read_cost_per_million_usd: Decimal = Field(
        default=Decimal("0"), ge=0, description="Health-model cache-read price."
    )
    runtime_mode: RuntimeMode = Field(
        default=RuntimeMode.PRODUCTION, description="Production or zero-cost development."
    )
    log_level: str = Field(default="INFO", description="Application logging level.")
    corpus_source_dir: Path = Field(
        default=PROMPT_CORPUS_DIR,
        description="Directory containing the three curated MASTER CSV files.",
    )

    @model_validator(mode="after")
    def phase2_embedding_contract(self) -> "RuntimeSettings":
        """Fail before I/O when settings disagree with the deployed vector schema."""
        if self.embedding_dimensions != 1024:
            raise ValueError(
                "embedding dimension changes require a migration and full re-embed"
            )
        prices = (
            self.anthropic_fast_input_cost_per_million_usd,
            self.anthropic_fast_output_cost_per_million_usd,
            self.anthropic_fast_cache_write_cost_per_million_usd,
            self.anthropic_fast_cache_read_cost_per_million_usd,
            self.anthropic_behavior_input_cost_per_million_usd,
            self.anthropic_behavior_output_cost_per_million_usd,
            self.anthropic_behavior_cache_write_cost_per_million_usd,
            self.anthropic_behavior_cache_read_cost_per_million_usd,
            self.anthropic_health_input_cost_per_million_usd,
            self.anthropic_health_output_cost_per_million_usd,
            self.anthropic_health_cache_write_cost_per_million_usd,
            self.anthropic_health_cache_read_cost_per_million_usd,
        )
        if self.runtime_mode is RuntimeMode.PRODUCTION and any(
            price <= 0 for price in prices
        ):
            raise ValueError(
                "production spend-cap pricing must configure every model/cache rate"
            )
        tier_prices = (
            (
                self.anthropic_fast_model,
                prices[0:4],
            ),
            (
                self.anthropic_behavior_model,
                prices[4:8],
            ),
            (
                self.anthropic_health_model,
                prices[8:12],
            ),
        )
        rates_by_model: dict[str, tuple[Decimal, ...]] = {}
        for model, rates in tier_prices:
            if model in rates_by_model and rates_by_model[model] != rates:
                raise ValueError(
                    "the same Anthropic model id cannot have conflicting tier prices"
                )
            rates_by_model[model] = rates
        return self


def load_runtime_settings() -> RuntimeSettings:
    """Allow a complete zero-secret development boot while production stays strict."""
    if os.getenv("RUNTIME_MODE", "").casefold() == RuntimeMode.DEVELOPMENT.value:
        return RuntimeSettings(
            database_url=SecretStr(
                os.getenv("DATABASE_URL", "postgresql://development-unused")
            ),
            supabase_url=os.getenv(
                "SUPABASE_URL", "https://development.invalid"
            ),
            supabase_anon_key=SecretStr(
                os.getenv("SUPABASE_ANON_KEY", "development-unused")
            ),
            supabase_service_role_key=SecretStr(
                os.getenv("SUPABASE_SERVICE_ROLE_KEY", "development-unused")
            ),
            anthropic_api_key=SecretStr(
                os.getenv("ANTHROPIC_API_KEY", "development-unused")
            ),
            anthropic_fast_model=os.getenv(
                "ANTHROPIC_FAST_MODEL", "development-fast"
            ),
            anthropic_reasoning_model=os.getenv(
                "ANTHROPIC_REASONING_MODEL", "development-reasoning"
            ),
            reranker_url=os.getenv(
                "RERANKER_URL", "https://development.invalid/rerank"
            ),
            reranker_api_key=SecretStr(
                os.getenv("RERANKER_API_KEY", "development-unused")
            ),
            hard_spend_cap_usd=Decimal(os.getenv("HARD_SPEND_CAP_USD", "1")),
            runtime_mode=RuntimeMode.DEVELOPMENT,
        )
    return RuntimeSettings()  # type: ignore[call-arg]
