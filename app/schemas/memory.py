"""Cat-isolated session and long-term memory contracts."""

from uuid import UUID

from pydantic import AwareDatetime, Field, PositiveInt

from app.schemas.base import ContractModel
from app.schemas.enums import Corner, MessageRole


class SessionMessage(ContractModel):
    """A typed conversational message; no downstream string parsing is permitted."""

    role: MessageRole = Field(description="Author role for the persisted message.")
    content: str = Field(min_length=1, description="User-visible message content.")


class SessionMemory(ContractModel):
    """One active session whose contents belong to exactly one cat."""

    session_id: UUID = Field(description="Session identifier.")
    cat_id: UUID = Field(
        description="Required isolation key; session reads must filter on this value."
    )
    corner: Corner = Field(description="Product corner that owns the session.")
    messages: list[SessionMessage] = Field(description="Ordered session messages.")
    rolling_summary: str | None = Field(
        default=None, description="Optional compact summary of older session messages."
    )
    updated_at: AwareDatetime = Field(description="Timezone-aware last update time.")


class LongTermMemory(ContractModel):
    """A cat-specific summary that must never be shared across cats."""

    id: UUID = Field(description="Long-term memory identifier.")
    cat_id: UUID = Field(
        description="Required isolation key; retrieval must filter on this value."
    )
    summary: str = Field(min_length=1, description="Validated memory summary text.")
    source_session_id: UUID = Field(description="Session from which this memory derives.")
    created_at: AwareDatetime = Field(description="Timezone-aware creation time.")
    embedding_reference: UUID = Field(
        description="Opaque reference to this memory row's stored embedding."
    )


class MemoryQuery(ContractModel):
    """Cat isolation enforcement point: every memory lookup requires an explicit cat_id."""

    cat_id: UUID = Field(
        description="Non-optional isolation key applied to every memory query."
    )
    query: str = Field(min_length=1, description="Semantic memory lookup text.")
    limit: PositiveInt = Field(
        default=5, le=50, description="Maximum matching memories to return."
    )


class MemoryResult(ContractModel):
    """Cat isolation enforcement point: results carry the cat_id they were filtered by."""

    cat_id: UUID = Field(
        description="Non-optional isolation key echoed from the memory query."
    )
    profile_facts: list[str] = Field(
        description="Relevant facts from only the active cat profile."
    )
    relevant_summaries: list[LongTermMemory] = Field(
        description="Relevant long-term summaries belonging only to this cat."
    )

