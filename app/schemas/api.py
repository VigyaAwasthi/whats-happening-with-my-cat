"""Typed request and response models for the declared HTTP surface."""

from datetime import date
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, Field, SecretStr, model_validator

from app.schemas.base import ContractModel
from app.schemas.corpora import FunFact
from app.schemas.domain import (
    Account,
    CatAge,
    CatProfile,
    CatRoster,
    CatTheme,
    CatWeight,
    Moment,
)
from app.schemas.enums import (
    AuthStatus,
    CatSex,
    Corner,
    EnergyLevel,
    FeedbackThumb,
    MomentKind,
)
from app.schemas.llm import BehaviorInterpretation, SymptomIntake, TriageResult
from app.schemas.memory import LongTermMemory, SessionMemory


class AuthSessionRequest(ContractModel):
    """Credentials passed to a thin Supabase Auth session wrapper."""

    email: str = Field(min_length=3, description="Account email address.")
    password: SecretStr = Field(min_length=8, description="Account password.")


class AuthSessionResponse(ContractModel):
    """Result of an auth attempt: either a live session or a pending confirmation.

    With Supabase email confirmation enabled — required in production — sign-up
    does **not** return session tokens. The identity exists but is unusable
    until the emailed link is followed. The previous contract could not express
    that state: it required tokens on every response, so the only way to
    satisfy it was to leave email confirmation switched off.

    ``status`` is the discriminator. Callers must branch on it before reading
    ``access_token``; the validator guarantees the token fields are all present
    together or all absent together, so there is no half-populated session.
    """

    status: AuthStatus = Field(
        default=AuthStatus.ACTIVE,
        description="Whether a usable session was issued or confirmation is pending.",
    )
    access_token: str | None = Field(
        default=None,
        description="Short-lived Supabase access token; null while unconfirmed.",
    )
    refresh_token: str | None = Field(
        default=None,
        description="Supabase refresh token; null while unconfirmed.",
    )
    expires_in_seconds: Annotated[
        int | None,
        Field(default=None, gt=0, description="Access-token lifetime in seconds."),
    ] = None

    @model_validator(mode="after")
    def session_material_matches_status(self) -> "AuthSessionResponse":
        """Never emit a partially populated session for either status."""
        material = (self.access_token, self.refresh_token, self.expires_in_seconds)
        if self.status is AuthStatus.ACTIVE:
            if any(value is None or value == "" for value in material):
                raise ValueError(
                    "an active auth session requires access token, refresh token, "
                    "and expiry"
                )
        elif any(value is not None for value in material):
            raise ValueError(
                "a confirmation-pending response must not carry session material"
            )
        return self


class CatCreateRequest(ContractModel):
    """Create one cat under the authenticated account."""

    cat_id: UUID = Field(
        description="Client-supplied cat identifier and mandatory isolation key."
    )
    name: str = Field(min_length=1, max_length=100, description="Cat display name.")
    age: CatAge = Field(description="Owner-reported structured age.")
    breed: str | None = Field(default=None, description="Optional free-text breed.")
    sex: CatSex = Field(
        default=CatSex.UNKNOWN,
        description="Optional owner-reported sex; defaults to unknown.",
    )
    weight: CatWeight = Field(description="Owner-reported structured weight.")
    energy_level: EnergyLevel = Field(description="Bounded energy level.")
    common_patterns: str = Field(description="Free-text common behavior patterns.")
    known_conditions: list[str] = Field(
        description="Owner-reported known conditions."
    )
    photo_references: list[str] = Field(
        description="Supabase Storage object keys for profile photos."
    )
    theme: CatTheme = Field(description="Per-cat UI theme.")


class CatPatchRequest(ContractModel):
    """Patch the active cat; cat_id cannot be omitted or replaced."""

    cat_id: UUID = Field(description="Mandatory active-cat isolation key.")
    name: str | None = Field(
        default=None, min_length=1, max_length=100, description="Replacement display name."
    )
    age: CatAge | None = Field(default=None, description="Replacement structured age.")
    breed: str | None = Field(default=None, description="Replacement free-text breed.")
    sex: CatSex | None = Field(
        default=None,
        description="Replacement owner-reported sex; use unknown when unsure.",
    )
    weight: CatWeight | None = Field(
        default=None, description="Replacement structured weight."
    )
    energy_level: EnergyLevel | None = Field(
        default=None, description="Replacement energy level."
    )
    common_patterns: str | None = Field(
        default=None, description="Replacement common-pattern description."
    )
    known_conditions: list[str] | None = Field(
        default=None, description="Replacement known-condition list."
    )
    photo_references: list[str] | None = Field(
        default=None, description="Replacement profile-photo storage keys."
    )
    theme: CatTheme | None = Field(default=None, description="Replacement UI theme.")


class CatListRequest(ContractModel):
    """List account cats while carrying an explicit active-cat scope."""

    cat_id: UUID = Field(description="Mandatory active-cat isolation key.")


class CatDeleteRequest(ContractModel):
    """Delete exactly one explicitly scoped cat."""

    cat_id: UUID = Field(description="Mandatory cat identifier to delete.")


class CatResponse(ContractModel):
    """Response containing one cat profile."""

    cat: CatProfile = Field(description="Created or updated cat profile.")


class CatListResponse(CatRoster):
    """Account cat list with the same hard ten-cat schema bound."""


class DeleteResponse(ContractModel):
    """Typed confirmation for a resource deletion."""

    deleted_id: UUID = Field(description="Identifier of the deleted resource.")
    deleted: bool = Field(description="Whether deletion completed.")


class BehaviorChatRequest(ContractModel):
    """Behavior-corner message scoped to one active cat and session."""

    cat_id: UUID = Field(description="Mandatory active-cat isolation key.")
    message: str = Field(min_length=1, description="Owner's behavior question.")
    session_id: UUID = Field(description="Behavior session identifier.")


class BehaviorChatResponse(ContractModel):
    """Validated behavior interpretation returned by the chat endpoint."""

    session_id: UUID = Field(
        description="Effective session id, including a replacement after a scope switch."
    )
    result: BehaviorInterpretation = Field(
        description="Structured interpretation after code-controlled decisions."
    )


class HealthChatRequest(ContractModel):
    """Health-corner input scoped to one cat, with structured intake when available."""

    cat_id: UUID = Field(description="Mandatory active-cat isolation key.")
    message: str | None = Field(
        default=None, description="Optional owner symptom description."
    )
    intake: SymptomIntake | None = Field(
        default=None, description="Optional pre-structured symptom intake."
    )
    session_id: UUID = Field(description="Health session identifier.")

    @model_validator(mode="after")
    def require_message_or_intake(self) -> "HealthChatRequest":
        """Reject empty health requests before any safety gate is considered."""
        if self.message is None and self.intake is None:
            raise ValueError("message or intake is required")
        return self


class HealthChatResponse(ContractModel):
    """Retrieval-locked triage response."""

    session_id: UUID = Field(
        description="Effective session id, including a replacement after a scope switch."
    )
    result: TriageResult = Field(description="Validated grounded triage result.")


class FunFactListRequest(ContractModel):
    """Request personalized facts for exactly one active cat."""

    cat_id: UUID = Field(description="Mandatory active-cat isolation key.")
    tags: list[str] = Field(
        default_factory=list, description="Active cat personalization tags."
    )
    exclude_ids: list[str] = Field(
        default_factory=list, description="Fact identifiers not to return."
    )


class FunFactDetailRequest(ContractModel):
    """Fetch one fact while retaining explicit cat scope."""

    cat_id: UUID = Field(description="Mandatory active-cat isolation key.")


class FunFactListResponse(ContractModel):
    """Curated fact cards selected for an active cat."""

    facts: list[FunFact] = Field(description="Matching curated fact cards.")


class FunFactDetailResponse(FunFact):
    """Expanded fact response required by the UI contract."""

    detail: str = Field(
        min_length=1, description="Longer pre-written expansion shown on card tap."
    )


class MomentListRequest(ContractModel):
    """List scrapbook items for exactly one cat."""

    cat_id: UUID = Field(description="Mandatory active-cat isolation key.")


class MomentCreateRequest(ContractModel):
    """Create a scrapbook item without exposing it to any AI context."""

    cat_id: UUID = Field(description="Mandatory active-cat isolation key.")
    kind: MomentKind = Field(description="Kind of scrapbook item.")
    title: str = Field(min_length=1, max_length=200, description="Moment title.")
    body: str | None = Field(default=None, description="Optional note body.")
    media_key: str | None = Field(
        default=None, description="Optional Supabase Storage object key."
    )
    event_date: date | None = Field(
        default=None, description="Optional date represented by the item."
    )


class MomentDeleteRequest(ContractModel):
    """Delete one scrapbook item within an explicit cat scope."""

    cat_id: UUID = Field(description="Mandatory active-cat isolation key.")
    moment_id: UUID = Field(description="Moment identifier to delete.")


class MomentResponse(ContractModel):
    """Response containing one scrapbook item."""

    moment: Moment = Field(description="Created scrapbook item.")


class MomentListResponse(ContractModel):
    """Response containing scrapbook items for exactly one cat."""

    cat_id: UUID = Field(description="Isolation key used for the result.")
    moments: list[Moment] = Field(description="Items belonging only to this cat.")


class FeedbackRequest(ContractModel):
    """Thumb and optional helpfulness score for a cat-scoped session."""

    cat_id: UUID = Field(description="Mandatory active-cat isolation key.")
    session_id: UUID = Field(description="Session receiving feedback.")
    corner: Corner = Field(description="Corner that produced the response.")
    thumb: FeedbackThumb = Field(description="Binary helpful or unhelpful choice.")
    helpfulness_score: Annotated[
        int | None,
        Field(
            default=None,
            ge=1,
            le=5,
            description="Optional one-to-five helpfulness score.",
        ),
    ]


class FeedbackRecord(FeedbackRequest):
    """Persisted feedback record included in account export."""

    id: UUID = Field(description="Feedback identifier.")
    created_at: AwareDatetime = Field(description="Timezone-aware creation time.")


class FeedbackResponse(ContractModel):
    """Typed feedback-write confirmation."""

    feedback: FeedbackRecord = Field(description="Persisted feedback record.")


class AccountExportResponse(ContractModel):
    """Complete authenticated-account export for portability and deletion review."""

    account: Account = Field(description="Human account and global preferences.")
    cats: Annotated[
        list[CatProfile],
        Field(max_length=10, description="All cat profiles owned by the account."),
    ]
    sessions: list[SessionMemory] = Field(description="All cat-scoped sessions.")
    long_term_memory: list[LongTermMemory] = Field(
        description="All cat-scoped long-term summaries."
    )
    moments: list[Moment] = Field(description="All scrapbook data, never AI context.")
    feedback: list[FeedbackRecord] = Field(description="All submitted feedback.")


class AccountDeleteResponse(ContractModel):
    """Confirmation that the authenticated account cascade completed."""

    account_id: UUID = Field(description="Deleted account identifier.")
    deleted: bool = Field(description="Whether full account deletion completed.")
