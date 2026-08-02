"""The record that makes a thumbs-down diagnosable.

A rating that identifies only a session tells you a conversation went badly. It
does not tell you *which* of the two failure modes happened, and they need
opposite fixes:

* retrieval surfaced the wrong entries -> fix the corpus, the aliases, or routing
* retrieval was right and generation synthesized badly -> fix the prompt

Distinguishing them after the fact requires the retrieved set, the scores that
produced it, the prompt version, and the model id, all captured at the moment
the answer was produced. That is what this module models.
"""

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import AwareDatetime, Field, NonNegativeFloat, NonNegativeInt

from app.schemas.base import ContractModel
from app.schemas.enums import Corner


class RetrievalStage(str, Enum):
    """The three points where the candidate set changes shape."""

    HYBRID_CANDIDATES = "hybrid_candidates"
    POST_RERANK = "post_rerank"
    FINAL_CONTEXT = "final_context"


class TracedRetrievalEntry(ContractModel):
    """One entry's position and scores at one retrieval stage.

    Recorded per stage rather than only at the end, because "the reranker
    demoted the right answer" and "the right answer was never a candidate" look
    identical in the final set and have completely different fixes.
    """

    stage: RetrievalStage = Field(description="Which retrieval stage this row describes.")
    entry_id: str = Field(min_length=1, description="Corpus entry identifier.")
    rank: NonNegativeInt = Field(description="Zero-based position within the stage.")
    lexical: NonNegativeFloat | None = Field(
        default=None, description="Lexical channel score, when known at this stage."
    )
    semantic: NonNegativeFloat | None = Field(
        default=None, description="Vector channel score, when known at this stage."
    )
    rerank: NonNegativeFloat | None = Field(
        default=None, description="Cross-encoder score, when the reranker ran."
    )


class RetrievalConsensus(ContractModel):
    """Which retrieval signals agreed on the winning entry.

    Behavior mode selection is decided by signal agreement, not by score
    magnitude, so "why did this answer come back as general knowledge instead of
    corpus grounded" is unanswerable without this.
    """

    top_entry_id: str | None = Field(
        default=None, description="Entry the fused ranking placed first."
    )
    semantic_agrees: bool = Field(
        default=False, description="Vector channel also ranked it first."
    )
    lexical_agrees: bool = Field(
        default=False, description="Lexical channel also ranked it first."
    )
    rerank_agrees: bool = Field(
        default=False, description="Cross-encoder also ranked it first."
    )
    coverage_ratio: float | None = Field(
        default=None,
        description="Deterministic query-term coverage of the winning entry.",
    )


class GroundednessOutcome(str, Enum):
    """What the groundedness validator did to the draft."""

    NOT_APPLICABLE = "not_applicable"
    PASSED = "passed"
    CLAIMS_STRIPPED = "claims_stripped"
    REGENERATED = "regenerated"
    FAILED_FELL_BACK = "failed_fell_back"


class ModelCallTrace(ContractModel):
    """One model call: which model, what it cost, whether it validated."""

    purpose: str = Field(min_length=1, description="Call site: fast, behavior, or health.")
    model: str = Field(min_length=1, description="Exact model identifier used.")
    prompt_version: str = Field(
        min_length=1, description="Version of the system prompt at this call site."
    )
    input_tokens: NonNegativeInt = Field(default=0, description="Billed input tokens.")
    output_tokens: NonNegativeInt = Field(default=0, description="Billed output tokens.")
    cache_read_tokens: NonNegativeInt = Field(
        default=0, description="Tokens served from the prompt cache."
    )
    cache_write_tokens: NonNegativeInt = Field(
        default=0, description="Tokens written to the prompt cache."
    )
    latency_ms: NonNegativeFloat = Field(default=0.0, description="Wall time for the call.")
    validation: str = Field(
        default="unknown", description="passed, failed, or unknown."
    )
    attempts: NonNegativeInt = Field(default=1, description="Requests made, including retries.")
    cost_usd: float = Field(default=0.0, description="Computed cost for this call.")


class StageLatency(ContractModel):
    """Where the wall time went. Retrieval and generation degrade differently."""

    retrieval_ms: NonNegativeFloat = Field(default=0.0, description="Retrieval time.")
    generation_ms: NonNegativeFloat = Field(default=0.0, description="Model generation time.")
    validation_ms: NonNegativeFloat = Field(default=0.0, description="Groundedness time.")
    total_ms: NonNegativeFloat = Field(default=0.0, description="End-to-end handler time.")


class GenerationTrace(ContractModel):
    """Everything needed to explain one answer, three days later.

    This record contains user content (the query and the produced answer), so it
    is subject to account export, the account delete cascade, and a retention
    window. See `TRACE_RETENTION_DAYS` and `app/ops/traces.py`.
    """

    generation_id: UUID = Field(description="Server-issued identifier returned to the client.")
    cat_id: UUID = Field(description="Cat isolation key.")
    session_id: UUID = Field(description="Session the exchange belongs to.")
    corner: Corner = Field(description="Which corner produced this.")
    created_at: AwareDatetime = Field(description="When the answer was produced.")

    query: str = Field(default="", description="The user query exactly as sent.")
    response_text: str = Field(
        default="",
        description="The answer as served. Kept here, never in application logs.",
    )

    retrieval: list[TracedRetrievalEntry] = Field(
        default_factory=list, description="Per-stage retrieval detail."
    )
    consensus: RetrievalConsensus = Field(
        default_factory=RetrievalConsensus, description="Which signals agreed."
    )

    answer_mode: str | None = Field(
        default=None, description="Behavior corner answer mode."
    )
    response_kind: str | None = Field(
        default=None, description="Health corner response kind."
    )

    model_calls: list[ModelCallTrace] = Field(
        default_factory=list, description="Every model call made for this answer."
    )
    prompt_version: str = Field(
        default="v1", description="Prompt bundle version for the primary call site."
    )

    total_input_tokens: NonNegativeInt = Field(default=0, description="Summed input tokens.")
    total_output_tokens: NonNegativeInt = Field(default=0, description="Summed output tokens.")
    cache_read_tokens: NonNegativeInt = Field(default=0, description="Summed cache reads.")
    cache_write_tokens: NonNegativeInt = Field(default=0, description="Summed cache writes.")
    cost_usd: float = Field(default=0.0, description="Computed cost for the whole answer.")

    latency: StageLatency = Field(
        default_factory=StageLatency, description="Per-stage wall time."
    )

    groundedness: GroundednessOutcome = Field(
        default=GroundednessOutcome.NOT_APPLICABLE,
        description="What validation did to the draft.",
    )
    red_flag_fired: bool = Field(
        default=False, description="Whether the deterministic emergency gate matched."
    )
    red_flag_rules: list[str] = Field(
        default_factory=list, description="Which deterministic rules matched."
    )
    canned_response_id: str | None = Field(
        default=None, description="Canned safety response served, when one was."
    )
    model_call_count: NonNegativeInt = Field(
        default=0,
        description="Model calls made; zero proves the deterministic gate short-circuited.",
    )

    def entries_at(self, stage: RetrievalStage) -> list[str]:
        """Entry ids recorded at one stage, in rank order."""
        return [
            item.entry_id
            for item in sorted(
                (row for row in self.retrieval if row.stage is stage),
                key=lambda row: row.rank,
            )
        ]


def utc_now() -> datetime:
    """Timezone-aware creation stamp, isolated so tests can freeze it."""
    from datetime import timezone

    return datetime.now(timezone.utc)
