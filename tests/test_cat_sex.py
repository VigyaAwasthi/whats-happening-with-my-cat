"""Cat-sex contract and CRUD coverage without changing safety behavior."""

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.repositories.application import (
    InMemoryApplicationRepository,
    development_account,
)
from app.schemas.api import CatCreateRequest, CatPatchRequest
from app.schemas.domain import CatAge, CatTheme, CatWeight
from app.schemas.enums import AgeUnit, CatSex, EnergyLevel, WeightUnit


ACCOUNT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def create_request(*, sex: CatSex = CatSex.UNKNOWN) -> CatCreateRequest:
    return CatCreateRequest(
        cat_id=uuid4(),
        name="Mochi",
        age=CatAge(value=3, unit=AgeUnit.YEARS),
        breed=None,
        sex=sex,
        weight=CatWeight(value=9, unit=WeightUnit.POUNDS),
        energy_level=EnergyLevel.THREE,
        common_patterns="Sleeps on the warm laundry.",
        known_conditions=[],
        photo_references=[],
        theme=CatTheme(primary_color="#E43D12", accent_color="#E43D12"),
    )


def test_cat_sex_defaults_to_unknown_and_round_trips_through_json() -> None:
    payload = create_request().model_dump()
    payload.pop("sex")
    request = CatCreateRequest.model_validate(payload)

    assert request.sex is CatSex.UNKNOWN
    assert CatCreateRequest.model_validate_json(request.model_dump_json()) == request


@pytest.mark.asyncio
async def test_cat_crud_exposes_and_updates_sex() -> None:
    repository = InMemoryApplicationRepository(development_account(ACCOUNT_ID))
    request = create_request()

    created = await repository.create_cat(ACCOUNT_ID, request)
    assert created is not None
    assert created.sex is CatSex.UNKNOWN

    updated = await repository.patch_cat(
        ACCOUNT_ID,
        CatPatchRequest(cat_id=request.cat_id, sex=CatSex.FEMALE),
    )
    assert updated is not None
    assert updated.sex is CatSex.FEMALE


def test_cat_sex_migration_is_nullable_and_defaults_existing_rows_to_unknown() -> None:
    migration = (
        Path(__file__).parents[1]
        / "db"
        / "migrations"
        / "007_add_cat_sex.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TYPE cat_sex AS ENUM ('male', 'female', 'unknown')" in migration
    assert "ADD COLUMN IF NOT EXISTS sex cat_sex DEFAULT 'unknown'" in migration
    assert "SET sex = 'unknown'" in migration
    assert "sex cat_sex NOT NULL" not in migration
