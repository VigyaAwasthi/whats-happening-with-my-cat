"""Contracts derived from the three curated corpus CSV files."""

from typing import Annotated

from pydantic import Field, model_validator

from app.schemas.base import ContractModel
from app.schemas.enums import (
    BehaviorCategory,
    BodySystem,
    ConfidenceLevel,
    FunFactCategory,
    FunFactTone,
    UrgencyTier,
)


class SourceRef(ContractModel):
    """A human-reviewable corpus source reference."""

    title: str = Field(min_length=1, description="Source title.")
    organization: str = Field(min_length=1, description="Publishing organization.")
    url: str | None = Field(
        default=None,
        description="Validated absolute source URL, or null when no link is confirmed.",
    )


class CorporaEntryBase(ContractModel):
    """Fields shared by the health and behavior CSV entries."""

    id: str = Field(min_length=1, description="Stable corpus entry slug.")
    topic: str = Field(min_length=1, description="Human-readable corpus topic.")
    aliases: list[str] = Field(description="Pipe-delimited CSV aliases after splitting.")
    keywords: list[str] = Field(description="Pipe-delimited CSV keywords after splitting.")
    summary: str = Field(min_length=1, description="Curated corpus summary.")


class HealthEntry(CorporaEntryBase):
    """Safety-critical health knowledge with deterministic gate inputs exposed."""

    body_system: BodySystem = Field(
        description="Controlled body-system filter used by retrieval and intake."
    )
    urgency_tier: UrgencyTier = Field(
        description="Curated urgency consumed by deterministic triage."
    )
    red_flags: list[str] = Field(
        min_length=1,
        description=(
            "Curated red-flag prose for evidence/context; coded safety rules are "
            "maintained separately and never inferred from this field."
        ),
    )
    when_to_see_vet: str = Field(
        min_length=1, description="Curated veterinary escalation guidance."
    )
    clarifying_questions: list[str] = Field(
        min_length=1,
        description="Curated questions used to narrow incomplete symptom descriptions.",
    )
    related_topics: list[str] = Field(
        description="Related corpus entry identifiers for retrieval expansion."
    )
    related_conditions: list[str] = Field(
        description="Curated differential context; it is not a diagnosis."
    )
    sources: Annotated[
        list[SourceRef],
        Field(
            min_length=1,
            max_length=3,
            description="One to three sources assembled from the CSV source triplets.",
        ),
    ]


class BehaviorEntry(CorporaEntryBase):
    """Curated behavior knowledge and its medical-signal handoff phrases."""

    category: BehaviorCategory = Field(description="Controlled behavior category.")
    confidence: ConfidenceLevel = Field(
        description="Curated confidence level for this interpretation."
    )
    medical_flag: list[str] = Field(
        description="Medical-signal phrases that can trigger a health-corner nudge."
    )
    clarifying_questions: list[str] = Field(
        min_length=1, description="Curated behavior follow-up questions."
    )
    related_topics: list[str] = Field(
        description="Related behavior corpus entry identifiers."
    )
    sources: Annotated[
        list[SourceRef],
        Field(
            min_length=1,
            max_length=2,
            description="One or two sources assembled from the CSV source triplets.",
        ),
    ]


class FunFact(ContractModel):
    """A curated fact card matching the columns in MASTER_fun_facts.csv."""

    # The card contract intentionally omits detail; ingestion requires it and the
    # expanded API response adds it without generating or modifying curated text.
    id: str = Field(min_length=1, description="Stable fun-fact entry slug.")
    fact: str = Field(min_length=1, description="Short pre-written fact card text.")
    category: FunFactCategory = Field(description="Controlled fact category.")
    tags: list[str] = Field(
        min_length=1,
        description="Personalization tags split from the pipe-delimited CSV field.",
    )
    tone: FunFactTone = Field(description="Controlled presentation tone.")
    personalization_hook: str = Field(
        min_length=1,
        description="Pre-written hook containing exactly one supported {name} slot.",
    )
    source_note: str = Field(min_length=1, description="Curator-facing source note.")
    source_url: str | None = Field(
        default=None,
        description="Validated absolute source URL, or null when no link is confirmed.",
    )

    @model_validator(mode="after")
    def validate_tags_and_hook(self) -> "FunFact":
        """Keep personalization templating and the observed CSV tag dialect bounded."""
        if self.personalization_hook.count("{name}") != 1:
            raise ValueError("personalization_hook must contain exactly one {name} slot")
        # Raw CSV validation accepts `general`; ingestion removes it while retaining
        # the canonical `all-cats` tag.
        invalid = [
            tag
            for tag in self.tags
            if tag not in {"all-cats", "general"} and ":" not in tag
        ]
        if invalid:
            raise ValueError(
                "tags must be key:value values, 'all-cats', or raw CSV 'general'"
            )
        return self
