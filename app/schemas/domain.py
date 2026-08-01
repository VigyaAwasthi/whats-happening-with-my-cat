"""Account, cat-profile, and scrapbook domain contracts."""

from datetime import date
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, Field, NonNegativeFloat, PositiveFloat

from app.schemas.base import ContractModel
from app.schemas.enums import AgeUnit, CatSex, EnergyLevel, MomentKind, WeightUnit


class NotificationSettings(ContractModel):
    """Global notification channel settings owned by an account."""

    settings: dict[str, bool] = Field(
        description="Notification-channel names mapped to enabled or disabled state."
    )


class AccountPreferences(ContractModel):
    """Global human preferences; this type intentionally contains no cat data."""

    tone: str = Field(min_length=1, description="Preferred global response tone.")
    notification_settings: NotificationSettings = Field(
        description="Global notification preferences."
    )
    locale: str = Field(min_length=2, description="BCP 47-style preferred locale.")


class Account(ContractModel):
    """One authenticated human account; cat-specific state must never live here."""

    id: UUID = Field(description="Internal account identifier.")
    auth_subject_id: UUID = Field(description="Supabase Auth subject identifier.")
    created_at: AwareDatetime = Field(description="Timezone-aware account creation time.")
    preferences: AccountPreferences = Field(
        description="Preferences that apply to the human account globally."
    )


class CatAge(ContractModel):
    """Structured age as owners commonly state it."""

    value: NonNegativeFloat = Field(description="Non-negative age magnitude.")
    unit: AgeUnit = Field(description="Unit associated with the age magnitude.")


class CatWeight(ContractModel):
    """Structured cat weight."""

    value: PositiveFloat = Field(description="Positive weight magnitude.")
    unit: WeightUnit = Field(description="Unit associated with the weight magnitude.")


class CatTheme(ContractModel):
    """Minimal, typed per-cat UI color theme."""

    primary_color: Annotated[
        str,
        Field(
            pattern=r"^#[0-9A-Fa-f]{6}$",
            description="Primary UI color as a six-digit hex value.",
        ),
    ]
    accent_color: Annotated[
        str,
        Field(
            pattern=r"^#[0-9A-Fa-f]{6}$",
            description="Accent UI color as a six-digit hex value.",
        ),
    ]


class CatProfile(ContractModel):
    """A single cat's profile, always owned by exactly one account."""

    id: UUID = Field(description="Cat identifier used as the mandatory isolation key.")
    account_id: UUID = Field(description="Owning account identifier.")
    name: str = Field(min_length=1, max_length=100, description="Cat display name.")
    age: CatAge = Field(description="Owner-reported structured age.")
    breed: str | None = Field(
        default=None, description="Optional owner-reported breed as free text."
    )
    sex: CatSex = Field(
        default=CatSex.UNKNOWN,
        description="Optional owner-reported sex; unknown is a valid lasting value.",
    )
    weight: CatWeight = Field(description="Owner-reported structured weight.")
    energy_level: EnergyLevel = Field(description="Bounded owner-reported energy level.")
    common_patterns: str = Field(
        description="Free-text description of this cat's common behavior patterns."
    )
    known_conditions: list[str] = Field(
        description="Owner-reported known conditions; not a diagnostic record."
    )
    photo_references: list[str] = Field(
        description="Supabase Storage object keys for profile photos."
    )
    theme: CatTheme = Field(description="UI theme that belongs only to this cat.")
    created_at: AwareDatetime = Field(description="Timezone-aware profile creation time.")
    updated_at: AwareDatetime = Field(description="Timezone-aware last update time.")


class CatRoster(ContractModel):
    """Account cat collection bounded to the product's hard ten-cat limit."""

    cats: Annotated[
        list[CatProfile],
        Field(
            max_length=10,
            description="Cat profiles for one account, with a hard maximum of ten.",
        ),
    ]


class Moment(ContractModel):
    """A scrapbook item that is never retrieved into any AI context."""

    id: UUID = Field(description="Moment identifier.")
    cat_id: UUID = Field(
        description="Non-optional cat isolation key for this scrapbook item."
    )
    kind: MomentKind = Field(description="Kind of scrapbook item.")
    title: str = Field(min_length=1, max_length=200, description="Moment title.")
    body: str | None = Field(default=None, description="Optional note body.")
    media_key: str | None = Field(
        default=None, description="Optional Supabase Storage object key."
    )
    event_date: date | None = Field(
        default=None, description="Optional date represented by this item."
    )
    created_at: AwareDatetime = Field(description="Timezone-aware creation time.")
