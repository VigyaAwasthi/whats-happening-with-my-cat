"""Internal validated records used only by the ingestion boundary."""

from uuid import UUID

from pydantic import Field, NonNegativeInt

from app.schemas.base import ContractModel
from app.schemas.corpora import FunFact


class FunFactIngestRow(FunFact):
    """Resolved Phase 2 fun-fact row with required curated expansion text."""

    detail: str = Field(
        min_length=1,
        description="Curated expansion stored exactly as present in the CSV.",
    )


class ChunkDraft(ContractModel):
    """Stable child chunk and the enriched text sent to the embedder."""

    id: UUID = Field(description="Deterministic chunk identifier.")
    parent_entry_id: str = Field(description="Stable parent corpus identifier.")
    chunk_text: str = Field(min_length=1, description="Semantic child text.")
    embedding_text: str = Field(
        min_length=1,
        description="Chunk text enriched with parent aliases and keywords.",
    )
    embedding: list[float] | None = Field(
        default=None, description="1024-dimensional embedding, or null after failure."
    )


class IngestionReport(ContractModel):
    """CLI-safe ingestion counters."""

    health_rows: NonNegativeInt = Field(description="Health parents upserted.")
    behavior_rows: NonNegativeInt = Field(description="Behavior parents upserted.")
    fun_fact_rows: NonNegativeInt = Field(description="Fun facts upserted.")
    chunks_created: NonNegativeInt = Field(description="Child chunks replaced.")
    tags_normalized: NonNegativeInt = Field(
        description="Fact rows whose redundant general tag was removed."
    )
    embedding_failures: NonNegativeInt = Field(
        description="Chunks persisted without an embedding after retries."
    )

