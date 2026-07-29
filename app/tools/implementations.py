"""Concrete persistence implementations of the remaining Phase 1 tool contracts."""

import logging
from collections.abc import Mapping

from app.db import Database
from app.repositories.application import _account, _cat_profile
from app.schemas.corpora import FunFact
from app.schemas.domain import CatProfile
from app.schemas.enums import ToolErrorCode
from app.tools.contracts import (
    AccountDeleteOutput,
    AccountLookupInput,
    AccountStoreInput,
    AccountStoreOutput,
    CatProfileDeleteOutput,
    CatProfileLookupInput,
    CatProfileStoreInput,
    CatProfileStoreOutput,
    FunFactFetcher,
    FunFactFetcherInput,
    FunFactFetcherOutput,
    ProfileStore,
    ToolError,
)

logger = logging.getLogger(__name__)


class PostgresProfileStore(ProfileStore):
    """Typed account/profile CRUD whose expected failures never escape the tool."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_account(self, request: AccountStoreInput) -> AccountStoreOutput:
        try:
            account = request.account
            row = await self._database.fetch_one(
                """
                INSERT INTO accounts (id, auth_subject_id, preferences, created_at)
                VALUES (%s, %s, %s::jsonb, %s)
                RETURNING *
                """,
                (
                    account.id,
                    account.auth_subject_id,
                    account.preferences.model_dump_json(),
                    account.created_at,
                ),
            )
            return _account_result(row)
        except Exception as exc:
            return AccountStoreOutput(
                error=_tool_error(exc, ToolErrorCode.CONFLICT, "account create failed")
            )

    async def get_account(self, request: AccountLookupInput) -> AccountStoreOutput:
        try:
            row = await self._database.fetch_one(
                "SELECT * FROM accounts WHERE id = %s", (request.account_id,)
            )
            if row is None:
                return AccountStoreOutput(error=_not_found("account not found"))
            return AccountStoreOutput(account=_account(dict(row)))
        except Exception as exc:
            return AccountStoreOutput(error=_storage_error(exc, "account lookup failed"))

    async def update_account(self, request: AccountStoreInput) -> AccountStoreOutput:
        try:
            account = request.account
            row = await self._database.fetch_one(
                """
                UPDATE accounts
                SET auth_subject_id = %s, preferences = %s::jsonb
                WHERE id = %s
                RETURNING *
                """,
                (
                    account.auth_subject_id,
                    account.preferences.model_dump_json(),
                    account.id,
                ),
            )
            if row is None:
                return AccountStoreOutput(error=_not_found("account not found"))
            return AccountStoreOutput(account=_account(dict(row)))
        except Exception as exc:
            return AccountStoreOutput(
                error=_tool_error(exc, ToolErrorCode.CONFLICT, "account update failed")
            )

    async def delete_account(
        self, request: AccountLookupInput
    ) -> AccountDeleteOutput:
        try:
            deleted = await self._database.execute(
                "DELETE FROM accounts WHERE id = %s", (request.account_id,)
            )
            if deleted != 1:
                return AccountDeleteOutput(error=_not_found("account not found"))
            return AccountDeleteOutput(deleted_account_id=request.account_id)
        except Exception as exc:
            return AccountDeleteOutput(
                error=_storage_error(exc, "account deletion failed")
            )

    async def create_cat(
        self, request: CatProfileStoreInput
    ) -> CatProfileStoreOutput:
        try:
            profile = request.profile
            row = await self._database.fetch_one(
                """
                INSERT INTO cat_profiles (
                    id, account_id, name, age_value, age_unit, breed,
                    weight_value, weight_unit, energy_level, common_patterns,
                    known_conditions, photo_references, theme, created_at, updated_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s::jsonb, %s, %s
                )
                RETURNING *
                """,
                _profile_values(profile),
            )
            return _cat_result(row)
        except Exception as exc:
            return CatProfileStoreOutput(
                error=_tool_error(
                    exc,
                    ToolErrorCode.CONFLICT,
                    "cat create failed; the profile may conflict or exceed the ten-cat cap",
                )
            )

    async def get_cat(
        self, request: CatProfileLookupInput
    ) -> CatProfileStoreOutput:
        try:
            row = await self._database.fetch_one(
                """
                SELECT * FROM cat_profiles
                WHERE id = %s AND account_id = %s
                """,
                (request.cat_id, request.account_id),
            )
            if row is None:
                return CatProfileStoreOutput(error=_not_found("cat profile not found"))
            return CatProfileStoreOutput(profile=_cat_profile(dict(row)))
        except Exception as exc:
            return CatProfileStoreOutput(
                error=_storage_error(exc, "cat profile lookup failed")
            )

    async def update_cat(
        self, request: CatProfileStoreInput
    ) -> CatProfileStoreOutput:
        try:
            profile = request.profile
            row = await self._database.fetch_one(
                """
                UPDATE cat_profiles SET
                    name = %s,
                    age_value = %s,
                    age_unit = %s,
                    breed = %s,
                    weight_value = %s,
                    weight_unit = %s,
                    energy_level = %s,
                    common_patterns = %s,
                    known_conditions = %s,
                    photo_references = %s,
                    theme = %s::jsonb,
                    updated_at = %s
                WHERE id = %s AND account_id = %s
                RETURNING *
                """,
                (
                    profile.name,
                    profile.age.value,
                    profile.age.unit.value,
                    profile.breed,
                    profile.weight.value,
                    profile.weight.unit.value,
                    profile.energy_level.value,
                    profile.common_patterns,
                    profile.known_conditions,
                    profile.photo_references,
                    profile.theme.model_dump_json(),
                    profile.updated_at,
                    profile.id,
                    profile.account_id,
                ),
            )
            if row is None:
                return CatProfileStoreOutput(error=_not_found("cat profile not found"))
            return CatProfileStoreOutput(profile=_cat_profile(dict(row)))
        except Exception as exc:
            return CatProfileStoreOutput(
                error=_tool_error(exc, ToolErrorCode.CONFLICT, "cat update failed")
            )

    async def delete_cat(
        self, request: CatProfileLookupInput
    ) -> CatProfileDeleteOutput:
        try:
            deleted = await self._database.execute(
                "DELETE FROM cat_profiles WHERE id = %s AND account_id = %s",
                (request.cat_id, request.account_id),
            )
            if deleted != 1:
                return CatProfileDeleteOutput(error=_not_found("cat profile not found"))
            return CatProfileDeleteOutput(deleted_cat_id=request.cat_id)
        except Exception as exc:
            return CatProfileDeleteOutput(
                error=_storage_error(exc, "cat deletion failed")
            )


class PostgresFunFactFetcher(FunFactFetcher):
    """Select curated cards only when the required active cat exists."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def fetch(self, request: FunFactFetcherInput) -> FunFactFetcherOutput:
        try:
            tags = list(dict.fromkeys([*request.active_cat_tags, "all-cats"]))
            rows = await self._database.fetch_all(
                """
                SELECT id, fact, category, tags, tone, personalization_hook,
                       source_note, source_url
                FROM fun_facts
                WHERE EXISTS (
                    SELECT 1 FROM cat_profiles WHERE id = %s
                )
                  AND tags && %s::text[]
                  AND NOT (id = ANY(%s::text[]))
                ORDER BY id
                """,
                (request.cat_id, tags, request.exclude_ids),
            )
            return FunFactFetcherOutput(
                facts=[FunFact.model_validate(dict(row)) for row in rows]
            )
        except Exception as exc:
            return FunFactFetcherOutput(
                error=_storage_error(exc, "fun-fact lookup failed")
            )


def _profile_values(profile: CatProfile) -> tuple[object, ...]:
    return (
        profile.id,
        profile.account_id,
        profile.name,
        profile.age.value,
        profile.age.unit.value,
        profile.breed,
        profile.weight.value,
        profile.weight.unit.value,
        profile.energy_level.value,
        profile.common_patterns,
        profile.known_conditions,
        profile.photo_references,
        profile.theme.model_dump_json(),
        profile.created_at,
        profile.updated_at,
    )


def _account_result(row: Mapping[str, object] | None) -> AccountStoreOutput:
    if row is None:
        return AccountStoreOutput(
            error=ToolError(
                code=ToolErrorCode.INTERNAL,
                message="account write returned no row",
                retryable=False,
            )
        )
    return AccountStoreOutput(account=_account(dict(row)))


def _cat_result(row: Mapping[str, object] | None) -> CatProfileStoreOutput:
    if row is None:
        return CatProfileStoreOutput(
            error=ToolError(
                code=ToolErrorCode.INTERNAL,
                message="cat write returned no row",
                retryable=False,
            )
        )
    return CatProfileStoreOutput(profile=_cat_profile(dict(row)))


def _not_found(message: str) -> ToolError:
    return ToolError(
        code=ToolErrorCode.NOT_FOUND,
        message=message,
        retryable=False,
    )


def _storage_error(exc: Exception, message: str) -> ToolError:
    return _tool_error(exc, ToolErrorCode.UNAVAILABLE, message, retryable=True)


def _tool_error(
    exc: Exception,
    code: ToolErrorCode,
    message: str,
    *,
    retryable: bool = False,
) -> ToolError:
    logger.warning("%s: %s", message, type(exc).__name__)
    return ToolError(code=code, message=message, retryable=retryable)
