"""Environment-only application configuration contracts."""

from decimal import Decimal

from pydantic import Field, PositiveInt, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed runtime settings; importing this module performs no I/O."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        case_sensitive=False,
    )

    database_url: SecretStr = Field(description="PostgreSQL connection URL.")
    supabase_url: str = Field(min_length=1, description="Supabase project URL.")
    supabase_anon_key: SecretStr = Field(description="Supabase public anonymous key.")
    supabase_service_role_key: SecretStr = Field(
        description="Server-only Supabase service-role key."
    )
    anthropic_api_key: SecretStr = Field(description="Anthropic API key.")
    anthropic_fast_model: str = Field(
        min_length=1, description="Fast-tier Anthropic model identifier."
    )
    embedding_model: str = Field(min_length=1, description="Embedding model identifier.")
    embedding_dimensions: PositiveInt = Field(
        default=1024, description="Vector dimension mirrored by migration 003."
    )
    reranker_url: str = Field(min_length=1, description="Cross-encoder service URL.")
    reranker_api_key: SecretStr = Field(description="Cross-encoder service API key.")
    reranker_model: str = Field(
        min_length=1, description="Cross-encoder model identifier."
    )
    retrieval_candidate_pool_size: PositiveInt = Field(
        default=40, description="Hybrid candidates retained before reranking."
    )
    retrieval_rerank_output_size: PositiveInt = Field(
        default=20, description="Candidates supplied to the cross-encoder reranker."
    )
    retrieval_final_context_size: PositiveInt = Field(
        default=5, description="Parent entries supplied to the final context."
    )
    hard_spend_cap_usd: Decimal = Field(
        gt=0, description="Hard application spend cap in US dollars."
    )

    @model_validator(mode="after")
    def retrieval_sizes_descend(self) -> "Settings":
        """Keep each retrieval stage structurally narrower than its input stage."""
        if not (
            self.retrieval_candidate_pool_size
            >= self.retrieval_rerank_output_size
            >= self.retrieval_final_context_size
        ):
            raise ValueError(
                "candidate pool must be >= rerank output >= final context size"
            )
        return self
