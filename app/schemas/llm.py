"""Validated structured outputs allowed from language-model boundaries."""

from pydantic import Field, NonNegativeInt, model_validator

from app.schemas.base import ContractModel
from app.schemas.enums import (
    AppetiteChange,
    BodySystem,
    ConfidenceLevel,
    TriageResponseKind,
    UrgencyTier,
    VomitingFrequency,
)


class HealthSignalCheck(ContractModel):
    """Model proposal for a behavior-to-health nudge; code makes the decision."""

    has_medical_signal: bool = Field(description="Whether a medical signal was detected.")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Bounded confidence in the signal classification."
    )
    matched_terms: list[str] = Field(
        description="Terms supporting the classification; this type contains no prose."
    )


class SymptomIntake(ContractModel):
    """Explicit symptom extraction; unknown values must remain unknown, never guessed."""

    body_systems: list[BodySystem] = Field(
        description="Zero or more explicitly supported body systems."
    )
    duration_hours: NonNegativeInt | None = Field(
        description="Known duration in hours, or null when the user did not say."
    )
    appetite_change: AppetiteChange = Field(
        description="Explicit appetite state, including an unknown value."
    )
    vomiting: VomitingFrequency = Field(
        description="Explicit vomiting state, including an unknown value."
    )
    litter_box_change: bool | None = Field(
        description="Observed litter-box change, or null when unknown."
    )
    breathing_change: bool | None = Field(
        description="Observed breathing change, or null when unknown."
    )
    lethargy: bool | None = Field(
        description="Observed lethargy, or null when unknown."
    )
    free_text_residual: str = Field(
        description="User details not represented by the structured fields."
    )


class Claim(ContractModel):
    """A health claim that is traceable to one retrieved corpus entry."""

    text: str = Field(min_length=1, description="User-facing grounded claim.")
    source_entry_id: str = Field(
        min_length=1, description="Retrieved health entry supporting this claim."
    )
    source_url: str | None = Field(
        default=None, description="Optional verbatim source URL for display."
    )


class TriageResult(ContractModel):
    """Retrieval-locked health output: code rejects every ungrounded model proposal."""

    severity: UrgencyTier = Field(description="Conservative resulting urgency tier.")
    claims: list[Claim] = Field(
        description="Health claims, each tied to an actually retrieved entry."
    )
    message: str = Field(
        min_length=1,
        description="Final user-facing prose after deterministic decisions are complete.",
    )
    retrieved_entry_ids: list[str] = Field(
        description="Health corpus entries supplied to the reasoning boundary."
    )
    response_kind: TriageResponseKind = Field(
        description="Explicit triage, coded emergency, or retrieval-refusal state."
    )

    @model_validator(mode="after")
    def enforce_retrieval_lock(self) -> "TriageResult":
        """Make unsupported medical claims structurally invalid, not merely discouraged."""
        retrieved = set(self.retrieved_entry_ids)
        unsupported = [
            claim.source_entry_id
            for claim in self.claims
            if claim.source_entry_id not in retrieved
        ]
        if unsupported:
            raise ValueError(
                "every claim source_entry_id must appear in retrieved_entry_ids"
            )
        if self.response_kind is TriageResponseKind.TRIAGE and not self.claims:
            raise ValueError(
                "claims must be non-empty when response_kind is triage"
            )
        return self


class BehaviorInterpretation(ContractModel):
    """Structured behavior proposal; code controls citations and medical handoff."""

    interpretation: str = Field(
        min_length=1, description="Final user-facing behavior interpretation."
    )
    confidence: ConfidenceLevel = Field(
        description="Explicit confidence label for the interpretation."
    )
    reasoning: str = Field(
        min_length=1, description="Structured rationale available for validation."
    )
    cited_entry_ids: list[str] = Field(
        description="Behavior corpus identifiers used by the interpretation."
    )
    suggested_clarifying_questions: list[str] = Field(
        description="Questions proposed for a later code-controlled interaction."
    )
    medical_nudge: bool = Field(
        description="Whether code should hand the user toward the health corner."
    )


class MemorySummary(ContractModel):
    """Typed summarization output before any memory write is considered."""

    summary: str = Field(min_length=1, description="Compact session summary.")
    salient_facts: list[str] = Field(
        description="Candidate cat-specific facts extracted from the session."
    )
    covers_message_count: NonNegativeInt = Field(
        description="Number of messages represented by the summary."
    )


class GroundednessVerdict(ContractModel):
    """Validated claim-support decision; code disposes of failing drafts."""

    passed: bool = Field(description="Whether all substantive claims are supported.")
    unsupported_claims: list[str] = Field(
        description="Claims not supported by supplied retrieved entries."
    )
    notes: str = Field(description="Validator notes for orchestration and audit.")

    @model_validator(mode="after")
    def passed_has_no_unsupported_claims(self) -> "GroundednessVerdict":
        """Prevent a passing verdict from carrying known unsupported claims."""
        if self.passed and self.unsupported_claims:
            raise ValueError("a passing verdict cannot contain unsupported claims")
        return self
