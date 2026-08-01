"""Contract invariants only; no service behavior, I/O, or network tests."""

from datetime import datetime, timezone
from pathlib import Path
from typing import get_args
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.schemas.api import CatListResponse
from app.schemas.domain import (
    CatAge,
    CatProfile,
    CatTheme,
    CatWeight,
)
from app.schemas.enums import (
    AgeUnit,
    AppetiteChange,
    BehaviorAnswerMode,
    BodySystem,
    ConfidenceLevel,
    EnergyLevel,
    TriageResponseKind,
    UrgencyTier,
    VomitingFrequency,
    WeightUnit,
)
from app.schemas.llm import (
    BehaviorCitation,
    BehaviorInterpretation,
    Claim,
    GroundednessVerdict,
    HealthSignalCheck,
    MemorySummary,
    SymptomIntake,
    TriageResult,
)
from app.schemas.memory import MemoryQuery, MemoryResult
from app.tools.contracts import MemoryRetrieverInput


def test_triage_rejects_claim_not_present_in_retrieved_ids() -> None:
    with pytest.raises(ValidationError, match="retrieved_entry_ids"):
        TriageResult(
            severity=UrgencyTier.URGENT,
            claims=[
                Claim(
                    text="A grounded-looking but unsupported claim.",
                    source_entry_id="not-retrieved",
                    source_url=None,
                )
            ],
            message="Please contact a veterinarian.",
            retrieved_entry_ids=["retrieved-entry"],
            response_kind=TriageResponseKind.TRIAGE,
        )


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (MemoryQuery, {"query": "favorite toy", "limit": 5}),
        (MemoryRetrieverInput, {"query": "favorite toy", "limit": 5}),
        (
            MemoryResult,
            {"profile_facts": [], "relevant_summaries": []},
        ),
    ],
)
def test_memory_boundaries_reject_missing_cat_id(
    model: type[MemoryQuery] | type[MemoryRetrieverInput] | type[MemoryResult],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="cat_id"):
        model.model_validate(payload)


def make_cat(account_id: UUID) -> CatProfile:
    now = datetime.now(timezone.utc)
    return CatProfile(
        id=uuid4(),
        account_id=account_id,
        name="Mochi",
        age=CatAge(value=3, unit=AgeUnit.YEARS),
        breed=None,
        weight=CatWeight(value=9, unit=WeightUnit.POUNDS),
        energy_level=EnergyLevel.THREE,
        common_patterns="Sleeps on the warm laundry.",
        known_conditions=[],
        photo_references=[],
        theme=CatTheme(primary_color="#112233", accent_color="#AABBCC"),
        created_at=now,
        updated_at=now,
    )


def test_cat_roster_rejects_more_than_ten_and_sql_has_enforcing_trigger() -> None:
    account_id = uuid4()
    with pytest.raises(ValidationError):
        CatListResponse(cats=[make_cat(account_id) for _ in range(11)])

    migration = (
        Path(__file__).parents[1] / "db" / "migrations" / "001_initial.sql"
    ).read_text(encoding="utf-8")
    assert "enforce_max_ten_cats_per_account" in migration
    assert ">= 10" in migration
    assert "pg_advisory_xact_lock" in migration


def test_all_structured_llm_outputs_round_trip_through_json() -> None:
    outputs = [
        Claim(
            text="A source-backed claim.",
            source_entry_id="entry-1",
            source_url=None,
        ),
        HealthSignalCheck(
            has_medical_signal=True,
            confidence=0.91,
            matched_terms=["not eating"],
        ),
        SymptomIntake(
            body_systems=[BodySystem.DIGESTIVE],
            duration_hours=None,
            appetite_change=AppetiteChange.UNKNOWN,
            vomiting=VomitingFrequency.NONE,
            litter_box_change=None,
            breathing_change=None,
            lethargy=None,
            free_text_residual="Owner did not state duration.",
        ),
        TriageResult(
            severity=UrgencyTier.URGENT,
            claims=[
                Claim(
                    text="Loss of appetite warrants veterinary contact.",
                    source_entry_id="not-eating",
                    source_url="https://example.test/source",
                )
            ],
            message="Trusted sources support contacting a veterinarian.",
            retrieved_entry_ids=["not-eating"],
            response_kind=TriageResponseKind.TRIAGE,
        ),
        BehaviorInterpretation(
            interpretation="This may be play-seeking behavior.",
            answer_mode=BehaviorAnswerMode.CORPUS_GROUNDED,
            confidence=ConfidenceLevel.VARIES_BY_CAT,
            reasoning="The behavior entry describes predatory play outlets.",
            cited_entry_ids=["play-needs"],
            retrieved_entry_ids=["play-needs"],
            cited_entries=[
                BehaviorCitation(
                    entry_id="play-needs",
                    title="Play needs",
                    organization="Example organization",
                    url=None,
                )
            ],
            suggested_clarifying_questions=["When does this happen?"],
            medical_nudge=False,
        ),
        MemorySummary(
            summary="Mochi likes wand toys.",
            salient_facts=["Prefers wand toys"],
            covers_message_count=4,
        ),
        GroundednessVerdict(
            passed=True,
            unsupported_claims=[],
            notes="All claims map to supplied entries.",
        ),
    ]

    for output in outputs:
        reconstructed = type(output).model_validate_json(output.model_dump_json())
        assert reconstructed == output


def test_cat_id_fields_are_required_and_never_nullable() -> None:
    models = [
        MemoryQuery,
        MemoryResult,
        MemoryRetrieverInput,
    ]
    for model in models:
        field = model.model_fields["cat_id"]
        assert field.is_required()
        assert type(None) not in get_args(field.annotation)
