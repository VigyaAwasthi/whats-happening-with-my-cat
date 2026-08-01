"""Phase 2 runtime settings layered over the fixed Phase 1 settings contract."""

import os
from decimal import Decimal
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, PositiveInt, SecretStr, model_validator

from app.config import Settings
from app.corpus_paths import PROMPT_CORPUS_DIR


REVIEWED_ANTHROPIC_MODELS: frozenset[str] = frozenset(
    {
        "claude-sonnet-5",
        "claude-haiku-4-5-20251001",
    }
)
"""Model identifiers explicitly reviewed for this deployment.

The Claude 5 family publishes complete identifiers with no dated snapshot
variants, so a date suffix cannot express "pinned" here — appending one
resolves to no model at all. This allowlist is the pin instead: production
refuses to start on any identifier that is not listed, so changing a model is
a reviewed source edit rather than an environment tweak. Adding an entry
requires re-running the routing and evaluation suites; see DEPLOYMENT.md.
"""

_PLACEHOLDER_MARKERS: tuple[str, ...] = (
    "your_",
    "changeme",
    "change-me",
    "replace_me",
    "replace-me",
    "todo",
    "xxx",
    "development-unused",
    "development.invalid",
    "local-unused",
)


class RuntimeMode(str, Enum):
    """Runtime backing mode."""

    PRODUCTION = "production"
    DEVELOPMENT = "development"


class RerankerMode(str, Enum):
    """Selectable reranker implementation."""

    HOSTED = "hosted"
    LOCAL = "local"


class SpendWindow(str, Enum):
    """Accounting period the hard spend cap is enforced over."""

    MONTHLY = "monthly"
    LIFETIME = "lifetime"


class RuntimeSettings(Settings):
    """Implementation settings added without changing the Phase 1 contract."""

    anthropic_fast_model: str = Field(
        default="claude-haiku-4-5-20251001",
        min_length=1,
        description="Date-pinned fast model for extraction, judging, and summaries.",
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
        default=0.70,
        ge=0,
        le=1,
        description="High-confidence threshold for a specific advisory on an answer.",
    )
    health_signal_medium_threshold: float = Field(
        default=0.40,
        ge=0,
        le=1,
        description="Medium-confidence threshold for a non-blocking health offer.",
    )
    behavior_grounding_min_query_coverage: float = Field(
        default=0.65,
        ge=0,
        le=1,
        description=(
            "Minimum deterministic query-term coverage for sourced behavior mode."
        ),
    )
    behavior_grounding_min_query_terms: PositiveInt = Field(
        default=2,
        description=(
            "Minimum matched content terms unless the query has one exact concept."
        ),
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
    spend_window: SpendWindow = Field(
        default=SpendWindow.MONTHLY,
        description="Accounting period the hard cap resets on; monthly is UTC calendar month.",
    )
    spend_warning_ratio: float = Field(
        default=0.8,
        gt=0,
        lt=1,
        description="Fraction of the cap that triggers an approaching-cap warning log.",
    )
    chat_rate_limit_per_minute: PositiveInt = Field(
        default=20,
        description="Per-account chat requests allowed per rolling minute; cost protection only.",
    )
    supabase_email_redirect_url: str = Field(
        default="http://localhost:3000/auth/confirmed",
        min_length=1,
        description="Where Supabase sends a user after they click the confirmation link.",
    )
    runtime_mode: RuntimeMode = Field(
        default=RuntimeMode.PRODUCTION, description="Production or zero-cost development."
    )
    log_level: str = Field(default="INFO", description="Application logging level.")
    log_format: str = Field(
        default="json",
        pattern="^(json|text)$",
        description="Structured JSON logs for deployment, plain text for local reading.",
    )
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
        if self.health_signal_medium_threshold >= self.health_signal_threshold:
            raise ValueError(
                "health signal medium threshold must be below the high threshold"
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

    @model_validator(mode="after")
    def anthropic_models_are_reviewed(self) -> "RuntimeSettings":
        """Refuse to run production on a model identifier nobody signed off on.

        This is the reproducibility guard. A model swap must be a source change
        to ``REVIEWED_ANTHROPIC_MODELS`` — never an environment variable edited
        against a live deployment — so that the routing and evaluation suites
        are re-run against whatever the deployment actually calls.
        """
        if self.runtime_mode is not RuntimeMode.PRODUCTION:
            return self
        configured = {
            "ANTHROPIC_FAST_MODEL": self.anthropic_fast_model,
            "ANTHROPIC_BEHAVIOR_MODEL": self.anthropic_behavior_model,
            "ANTHROPIC_HEALTH_MODEL": self.anthropic_health_model,
        }
        unreviewed = sorted(
            f"{name}={model!r}"
            for name, model in configured.items()
            if model not in REVIEWED_ANTHROPIC_MODELS
        )
        if unreviewed:
            raise ValueError(
                "unreviewed Anthropic model id(s): "
                + ", ".join(unreviewed)
                + "; add to REVIEWED_ANTHROPIC_MODELS and re-run the routing and "
                "evaluation suites before deploying"
            )
        return self

    @model_validator(mode="after")
    def cors_origins_are_bare_origins(self) -> "RuntimeSettings":
        """Reject anything a browser will not match against the ``Origin`` header."""
        for origin in self.cors_allowed_origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    f"CORS origin {origin!r} must be scheme://host[:port] with no "
                    "trailing slash, path, query, or fragment"
                )
            if origin != f"{parsed.scheme}://{parsed.netloc}":
                raise ValueError(f"CORS origin {origin!r} is not a normalized origin")
        return self

    @model_validator(mode="after")
    def production_settings_are_real(self) -> "RuntimeSettings":
        """Fail loudly at startup when a required production value is still a stub."""
        if self.runtime_mode is not RuntimeMode.PRODUCTION:
            return self
        required: dict[str, str] = {
            "DATABASE_URL": self.database_url.get_secret_value(),
            "SUPABASE_URL": self.supabase_url,
            "SUPABASE_ANON_KEY": self.supabase_anon_key.get_secret_value(),
            "SUPABASE_SERVICE_ROLE_KEY": self.supabase_service_role_key.get_secret_value(),
            "ANTHROPIC_API_KEY": self.anthropic_api_key.get_secret_value(),
            "VOYAGE_API_KEY": self.voyage_api_key.get_secret_value(),
            "SUPABASE_EMAIL_REDIRECT_URL": self.supabase_email_redirect_url,
        }
        if self.reranker_mode is RerankerMode.HOSTED:
            required["RERANKER_API_KEY"] = self.reranker_api_key.get_secret_value()
            required["RERANKER_URL"] = self.reranker_url
        unset = sorted(name for name, value in required.items() if not value.strip())
        placeholders = sorted(
            name
            for name, value in required.items()
            if value.strip()
            and any(marker in value.casefold() for marker in _PLACEHOLDER_MARKERS)
        )
        problems: list[str] = []
        if unset:
            problems.append("missing: " + ", ".join(unset))
        if placeholders:
            problems.append("still placeholder: " + ", ".join(placeholders))
        if self.reranker_mode is RerankerMode.LOCAL:
            problems.append(
                "RERANKER_MODE=local is a zero-cost development reranker and its "
                "scores are not calibrated relevance probabilities; production "
                "requires RERANKER_MODE=hosted"
            )
        if problems:
            raise ValueError("production configuration rejected — " + "; ".join(problems))
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
