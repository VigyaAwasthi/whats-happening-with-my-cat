"""Profile, fact, scrapbook, feedback, export, and auth implementations."""

import json
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

import httpx

from app.db import Database
from app.ingestion.csv_loader import load_fun_facts
from app.schemas.api import (
    AccountExportResponse,
    AuthSessionRequest,
    AuthSessionResponse,
    CatCreateRequest,
    CatPatchRequest,
    FeedbackRecord,
    FeedbackRequest,
    FunFactDetailResponse,
    MomentCreateRequest,
)
from app.schemas.corpora import FunFact
from app.schemas.domain import (
    Account,
    AccountPreferences,
    CatAge,
    CatProfile,
    CatTheme,
    CatWeight,
    Moment,
    NotificationSettings,
)
from app.schemas.enums import AgeUnit, EnergyLevel, WeightUnit
from app.schemas.memory import LongTermMemory, SessionMemory, SessionMessage

logger = logging.getLogger(__name__)


class AuthService(Protocol):
    """Supabase-backed or deterministic development auth."""

    async def sign_up(self, request: AuthSessionRequest) -> AuthSessionResponse:
        """Create an auth identity and session."""
        ...

    async def sign_in(self, request: AuthSessionRequest) -> AuthSessionResponse:
        """Create an auth session."""
        ...

    async def resolve_account(self, bearer_token: str | None) -> UUID | None:
        """Resolve a bearer token to the internal account id."""
        ...

    async def delete_identity(self, auth_subject_id: UUID) -> bool:
        """Delete the Supabase Auth identity for full account erasure."""
        ...


class ApplicationRepository(Protocol):
    """Account-level and cat-owned persistence used by HTTP routes."""

    async def list_cats(self, account_id: UUID) -> list[CatProfile]:
        ...

    async def create_cat(
        self, account_id: UUID, request: CatCreateRequest
    ) -> CatProfile | None:
        ...

    async def patch_cat(
        self, account_id: UUID, request: CatPatchRequest
    ) -> CatProfile | None:
        ...

    async def delete_cat(self, account_id: UUID, cat_id: UUID) -> bool:
        ...

    async def owns_cat(self, account_id: UUID, cat_id: UUID) -> bool:
        ...

    async def list_facts(
        self, cat_id: UUID, tags: list[str], exclude_ids: list[str]
    ) -> list[FunFact]:
        ...

    async def get_fact(
        self, cat_id: UUID, fact_id: str
    ) -> FunFactDetailResponse | None:
        ...

    async def list_moments(self, cat_id: UUID) -> list[Moment]:
        ...

    async def create_moment(self, request: MomentCreateRequest) -> Moment:
        ...

    async def delete_moment(self, cat_id: UUID, moment_id: UUID) -> bool:
        ...

    async def write_feedback(self, request: FeedbackRequest) -> FeedbackRecord:
        ...

    async def export_account(self, account_id: UUID) -> AccountExportResponse | None:
        ...

    async def delete_account(self, account_id: UUID) -> bool:
        ...

    async def get_auth_subject(self, account_id: UUID) -> UUID | None:
        ...


class SupabaseAuthService:
    """Thin Supabase Auth REST wrapper plus internal account resolution."""

    def __init__(
        self,
        *,
        supabase_url: str,
        anon_key: str,
        service_role_key: str,
        database: Database,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._anon_key = anon_key
        self._service_role_key = service_role_key
        self._database = database
        self._client = client or httpx.AsyncClient(
            base_url=supabase_url.rstrip("/"), timeout=20
        )

    async def sign_up(self, request: AuthSessionRequest) -> AuthSessionResponse:
        response = await self._client.post(
            "/auth/v1/signup",
            headers={"apikey": self._anon_key},
            json={
                "email": request.email,
                "password": request.password.get_secret_value(),
            },
        )
        response.raise_for_status()
        payload = response.json()
        user = payload.get("user")
        if not isinstance(user, dict) or user.get("id") is None:
            raise ValueError("Supabase sign-up returned no user identity")
        await self._database.execute(
            """
            INSERT INTO accounts (auth_subject_id, preferences)
            VALUES (%s, %s::jsonb)
            ON CONFLICT (auth_subject_id) DO NOTHING
            """,
            (
                user["id"],
                json.dumps(
                    {
                        "tone": "balanced",
                        "notification_settings": {"settings": {}},
                        "locale": "en-US",
                    }
                ),
            ),
        )
        # Deployment invariant: Supabase email confirmation remains disabled while
        # this response contract requires session tokens immediately after sign-up.
        return _auth_response(payload)

    async def sign_in(self, request: AuthSessionRequest) -> AuthSessionResponse:
        response = await self._client.post(
            "/auth/v1/token?grant_type=password",
            headers={"apikey": self._anon_key},
            json={
                "email": request.email,
                "password": request.password.get_secret_value(),
            },
        )
        response.raise_for_status()
        return _auth_response(response.json())

    async def resolve_account(self, bearer_token: str | None) -> UUID | None:
        if not bearer_token:
            return None
        response = await self._client.get(
            "/auth/v1/user",
            headers={
                "apikey": self._anon_key,
                "Authorization": f"Bearer {bearer_token}",
            },
        )
        if response.status_code != 200:
            return None
        subject = response.json().get("id")
        if subject is None:
            return None
        row = await self._database.fetch_one(
            "SELECT id FROM accounts WHERE auth_subject_id = %s", (subject,)
        )
        return None if row is None else row["id"]

    async def delete_identity(self, auth_subject_id: UUID) -> bool:
        response = await self._client.delete(
            f"/auth/v1/admin/users/{auth_subject_id}",
            headers={
                "apikey": self._service_role_key,
                "Authorization": f"Bearer {self._service_role_key}",
            },
        )
        return response.status_code in {200, 204, 404}


class DevelopmentAuthService:
    """Single-account zero-cost auth used only in development mode."""

    def __init__(self, account_id: UUID) -> None:
        self.account_id = account_id

    async def sign_up(self, request: AuthSessionRequest) -> AuthSessionResponse:
        return _development_auth_response()

    async def sign_in(self, request: AuthSessionRequest) -> AuthSessionResponse:
        return _development_auth_response()

    async def resolve_account(self, bearer_token: str | None) -> UUID:
        return self.account_id

    async def delete_identity(self, auth_subject_id: UUID) -> bool:
        return True


class PostgresApplicationRepository:
    """Explicit SQL implementation; cat-owned operations require cat_id."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def list_cats(self, account_id: UUID) -> list[CatProfile]:
        rows = await self._database.fetch_all(
            "SELECT * FROM cat_profiles WHERE account_id = %s ORDER BY created_at",
            (account_id,),
        )
        return [_cat_profile(row) for row in rows]

    async def create_cat(
        self, account_id: UUID, request: CatCreateRequest
    ) -> CatProfile | None:
        row = await self._database.fetch_one(
            """
            INSERT INTO cat_profiles (
                id, account_id, name, age_value, age_unit, breed,
                weight_value, weight_unit, energy_level, common_patterns,
                known_conditions, photo_references, theme
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING *
            """,
            (
                request.cat_id,
                account_id,
                request.name,
                request.age.value,
                request.age.unit.value,
                request.breed,
                request.weight.value,
                request.weight.unit.value,
                request.energy_level.value,
                request.common_patterns,
                request.known_conditions,
                request.photo_references,
                request.theme.model_dump_json(),
            ),
        )
        return None if row is None else _cat_profile(row)

    async def patch_cat(
        self, account_id: UUID, request: CatPatchRequest
    ) -> CatProfile | None:
        current = await self._database.fetch_one(
            "SELECT * FROM cat_profiles WHERE id = %s AND account_id = %s",
            (request.cat_id, account_id),
        )
        if current is None:
            return None
        age_value = (
            request.age.value if request.age is not None else current["age_value"]
        )
        age_unit = (
            request.age.unit.value
            if request.age is not None
            else current["age_unit"]
        )
        weight_value = (
            request.weight.value
            if request.weight is not None
            else current["weight_value"]
        )
        weight_unit = (
            request.weight.unit.value
            if request.weight is not None
            else current["weight_unit"]
        )
        theme = (
            request.theme.model_dump_json()
            if request.theme is not None
            else _json_text(current["theme"])
        )
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
                updated_at = now()
            WHERE id = %s AND account_id = %s
            RETURNING *
            """,
            (
                request.name if request.name is not None else current["name"],
                age_value,
                age_unit,
                (
                    request.breed
                    if "breed" in request.model_fields_set
                    else current["breed"]
                ),
                weight_value,
                weight_unit,
                (
                    request.energy_level.value
                    if request.energy_level is not None
                    else current["energy_level"]
                ),
                (
                    request.common_patterns
                    if request.common_patterns is not None
                    else current["common_patterns"]
                ),
                (
                    request.known_conditions
                    if request.known_conditions is not None
                    else current["known_conditions"]
                ),
                (
                    request.photo_references
                    if request.photo_references is not None
                    else current["photo_references"]
                ),
                theme,
                request.cat_id,
                account_id,
            ),
        )
        return None if row is None else _cat_profile(row)

    async def delete_cat(self, account_id: UUID, cat_id: UUID) -> bool:
        return (
            await self._database.execute(
                "DELETE FROM cat_profiles WHERE id = %s AND account_id = %s",
                (cat_id, account_id),
            )
            == 1
        )

    async def owns_cat(self, account_id: UUID, cat_id: UUID) -> bool:
        row = await self._database.fetch_one(
            "SELECT 1 AS owned FROM cat_profiles WHERE id = %s AND account_id = %s",
            (cat_id, account_id),
        )
        return row is not None

    async def list_facts(
        self, cat_id: UUID, tags: list[str], exclude_ids: list[str]
    ) -> list[FunFact]:
        effective_tags = list(dict.fromkeys([*tags, "all-cats"]))
        rows = await self._database.fetch_all(
            """
            SELECT id, fact, category, tags, tone, personalization_hook,
                   source_note, source_url
            FROM fun_facts
            WHERE EXISTS (SELECT 1 FROM cat_profiles WHERE id = %s)
              AND tags && %s::text[]
              AND NOT (id = ANY(%s::text[]))
            ORDER BY id
            """,
            (cat_id, effective_tags, exclude_ids),
        )
        return [FunFact.model_validate(row) for row in rows]

    async def get_fact(
        self, cat_id: UUID, fact_id: str
    ) -> FunFactDetailResponse | None:
        row = await self._database.fetch_one(
            """
            SELECT id, fact, detail, category, tags, tone, personalization_hook,
                   source_note, source_url
            FROM fun_facts
            WHERE id = %s
              AND EXISTS (SELECT 1 FROM cat_profiles WHERE id = %s)
            """,
            (fact_id, cat_id),
        )
        return None if row is None else FunFactDetailResponse.model_validate(row)

    async def list_moments(self, cat_id: UUID) -> list[Moment]:
        rows = await self._database.fetch_all(
            "SELECT * FROM moments WHERE cat_id = %s ORDER BY created_at DESC",
            (cat_id,),
        )
        return [Moment.model_validate(row) for row in rows]

    async def create_moment(self, request: MomentCreateRequest) -> Moment:
        row = await self._database.fetch_one(
            """
            INSERT INTO moments (cat_id, kind, title, body, media_key, event_date)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                request.cat_id,
                request.kind.value,
                request.title,
                request.body,
                request.media_key,
                request.event_date,
            ),
        )
        if row is None:
            raise ValueError("moment insert returned no row")
        return Moment.model_validate(row)

    async def delete_moment(self, cat_id: UUID, moment_id: UUID) -> bool:
        return (
            await self._database.execute(
                "DELETE FROM moments WHERE id = %s AND cat_id = %s",
                (moment_id, cat_id),
            )
            == 1
        )

    async def write_feedback(self, request: FeedbackRequest) -> FeedbackRecord:
        row = await self._database.fetch_one(
            """
            INSERT INTO feedback (
                cat_id, session_id, corner, thumb, helpfulness_score
            )
            SELECT %s, id, %s, %s, %s
            FROM sessions
            WHERE id = %s AND cat_id = %s
            RETURNING *
            """,
            (
                request.cat_id,
                request.corner.value,
                request.thumb.value,
                request.helpfulness_score,
                request.session_id,
                request.cat_id,
            ),
        )
        if row is None:
            raise ValueError("cat-scoped feedback session not found")
        return FeedbackRecord.model_validate(row)

    async def export_account(self, account_id: UUID) -> AccountExportResponse | None:
        account_row = await self._database.fetch_one(
            "SELECT * FROM accounts WHERE id = %s", (account_id,)
        )
        if account_row is None:
            return None
        cats = await self.list_cats(account_id)
        cat_ids = [cat.id for cat in cats]
        session_rows = await self._database.fetch_all(
            """
            SELECT * FROM sessions
            WHERE cat_id = ANY(%s)
            ORDER BY created_at
            """,
            (cat_ids,),
        )
        sessions: list[SessionMemory] = []
        for row in session_rows:
            message_rows = await self._database.fetch_all(
                """
                SELECT role, content FROM session_messages
                WHERE session_id = %s AND cat_id = %s
                ORDER BY created_at, id
                """,
                (row["id"], row["cat_id"]),
            )
            sessions.append(
                SessionMemory(
                    session_id=row["id"],
                    cat_id=row["cat_id"],
                    corner=row["corner"],
                    messages=[
                        SessionMessage.model_validate(message) for message in message_rows
                    ],
                    rolling_summary=row["rolling_summary"],
                    updated_at=row["updated_at"],
                )
            )
        memory_rows = await self._database.fetch_all(
            """
            SELECT id, cat_id, summary, source_session_id, created_at
            FROM long_term_memory
            WHERE cat_id = ANY(%s)
            ORDER BY created_at
            """,
            (cat_ids,),
        )
        scrapbook_rows = await self._database.fetch_all(
            "SELECT * FROM moments WHERE cat_id = ANY(%s) ORDER BY created_at",
            (cat_ids,),
        )
        feedback_rows = await self._database.fetch_all(
            "SELECT * FROM feedback WHERE cat_id = ANY(%s) ORDER BY created_at",
            (cat_ids,),
        )
        return AccountExportResponse(
            account=_account(account_row),
            cats=cats,
            sessions=sessions,
            long_term_memory=[
                LongTermMemory.model_validate(
                    {**row, "embedding_reference": row["id"]}
                )
                for row in memory_rows
            ],
            moments=[Moment.model_validate(row) for row in scrapbook_rows],
            feedback=[FeedbackRecord.model_validate(row) for row in feedback_rows],
        )

    async def delete_account(self, account_id: UUID) -> bool:
        return (
            await self._database.execute(
                "DELETE FROM accounts WHERE id = %s", (account_id,)
            )
            == 1
        )

    async def get_auth_subject(self, account_id: UUID) -> UUID | None:
        row = await self._database.fetch_one(
            "SELECT auth_subject_id FROM accounts WHERE id = %s", (account_id,)
        )
        return None if row is None else row["auth_subject_id"]


class InMemoryApplicationRepository:
    """Development repository with the same account/cat ownership boundaries."""

    def __init__(self, account: Account) -> None:
        self.account = account
        self.deleted = False
        self.cats: dict[UUID, CatProfile] = {}
        self.facts: dict[str, FunFactDetailResponse] = {}
        self.scrapbook: dict[UUID, list[Moment]] = {}
        self.feedback: list[FeedbackRecord] = []
        self.sessions: list[SessionMemory] = []
        self.long_term: list[LongTermMemory] = []

    async def list_cats(self, account_id: UUID) -> list[CatProfile]:
        return (
            list(self.cats.values())
            if not self.deleted and account_id == self.account.id
            else []
        )

    async def create_cat(
        self, account_id: UUID, request: CatCreateRequest
    ) -> CatProfile | None:
        if self.deleted or account_id != self.account.id or len(self.cats) >= 10:
            return None
        now = datetime.now(timezone.utc)
        profile = CatProfile(
            id=request.cat_id,
            account_id=account_id,
            name=request.name,
            age=request.age,
            breed=request.breed,
            weight=request.weight,
            energy_level=request.energy_level,
            common_patterns=request.common_patterns,
            known_conditions=request.known_conditions,
            photo_references=request.photo_references,
            theme=request.theme,
            created_at=now,
            updated_at=now,
        )
        self.cats[profile.id] = profile
        return profile

    async def patch_cat(
        self, account_id: UUID, request: CatPatchRequest
    ) -> CatProfile | None:
        current = self.cats.get(request.cat_id)
        if current is None or current.account_id != account_id:
            return None
        updates = {
            key: getattr(request, key)
            for key in request.model_fields_set
            if key != "cat_id"
        }
        profile = current.model_copy(
            update={**updates, "updated_at": datetime.now(timezone.utc)}
        )
        self.cats[profile.id] = profile
        return profile

    async def delete_cat(self, account_id: UUID, cat_id: UUID) -> bool:
        profile = self.cats.get(cat_id)
        if profile is None or profile.account_id != account_id:
            return False
        del self.cats[cat_id]
        return True

    async def owns_cat(self, account_id: UUID, cat_id: UUID) -> bool:
        profile = self.cats.get(cat_id)
        return (
            not self.deleted
            and profile is not None
            and profile.account_id == account_id
        )

    async def list_facts(
        self, cat_id: UUID, tags: list[str], exclude_ids: list[str]
    ) -> list[FunFact]:
        if self.deleted or cat_id not in self.cats:
            return []
        excluded = set(exclude_ids)
        requested = {*tags, "all-cats"}
        return [
            FunFact.model_validate(fact.model_dump(exclude={"detail"}))
            for fact in self.facts.values()
            if fact.id not in excluded
            and requested.intersection(fact.tags)
        ]

    async def get_fact(
        self, cat_id: UUID, fact_id: str
    ) -> FunFactDetailResponse | None:
        if self.deleted or cat_id not in self.cats:
            return None
        return self.facts.get(fact_id)

    async def list_moments(self, cat_id: UUID) -> list[Moment]:
        return list(self.scrapbook.get(cat_id, []))

    async def create_moment(self, request: MomentCreateRequest) -> Moment:
        item = Moment(
            id=uuid4(),
            cat_id=request.cat_id,
            kind=request.kind,
            title=request.title,
            body=request.body,
            media_key=request.media_key,
            event_date=request.event_date,
            created_at=datetime.now(timezone.utc),
        )
        self.scrapbook.setdefault(request.cat_id, []).append(item)
        return item

    async def delete_moment(self, cat_id: UUID, moment_id: UUID) -> bool:
        items = self.scrapbook.get(cat_id, [])
        kept = [item for item in items if item.id != moment_id]
        self.scrapbook[cat_id] = kept
        return len(kept) != len(items)

    async def write_feedback(self, request: FeedbackRequest) -> FeedbackRecord:
        record = FeedbackRecord(
            **request.model_dump(),
            id=uuid4(),
            created_at=datetime.now(timezone.utc),
        )
        self.feedback.append(record)
        return record

    async def export_account(self, account_id: UUID) -> AccountExportResponse | None:
        if self.deleted or account_id != self.account.id:
            return None
        return AccountExportResponse(
            account=self.account,
            cats=list(self.cats.values()),
            sessions=self.sessions,
            long_term_memory=self.long_term,
            moments=[
                item for items in self.scrapbook.values() for item in items
            ],
            feedback=self.feedback,
        )

    async def delete_account(self, account_id: UUID) -> bool:
        if self.deleted or account_id != self.account.id:
            return False
        self.cats.clear()
        self.scrapbook.clear()
        self.feedback.clear()
        self.sessions.clear()
        self.long_term.clear()
        self.deleted = True
        return True

    async def get_auth_subject(self, account_id: UUID) -> UUID | None:
        return (
            self.account.auth_subject_id
            if not self.deleted and account_id == self.account.id
            else None
        )

    def load_facts(self, source_path: Path) -> None:
        facts, _ = load_fun_facts(source_path)
        self.facts = {
            fact.id: FunFactDetailResponse.model_validate(fact.model_dump())
            for fact in facts
        }


def development_account(account_id: UUID) -> Account:
    """Create the single deterministic local account."""
    return Account(
        id=account_id,
        auth_subject_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        created_at=datetime.now(timezone.utc),
        preferences=AccountPreferences(
            tone="balanced",
            notification_settings=NotificationSettings(settings={}),
            locale="en-US",
        ),
    )


def _auth_response(payload: Mapping[str, Any]) -> AuthSessionResponse:
    session = payload.get("session")
    source = session if isinstance(session, dict) else payload
    return AuthSessionResponse(
        access_token=source["access_token"],
        refresh_token=source["refresh_token"],
        expires_in_seconds=source.get("expires_in", 3600),
    )


def _development_auth_response() -> AuthSessionResponse:
    return AuthSessionResponse(
        access_token="development-token",
        refresh_token="development-refresh",
        expires_in_seconds=3600,
    )


def _account(row: Mapping[str, Any]) -> Account:
    preferences = dict(row["preferences"])
    if "notification_settings" not in preferences:
        preferences["notification_settings"] = {"settings": {}}
    elif isinstance(preferences["notification_settings"], dict) and "settings" not in preferences["notification_settings"]:
        preferences["notification_settings"] = {
            "settings": preferences["notification_settings"]
        }
    return Account(
        id=row["id"],
        auth_subject_id=row["auth_subject_id"],
        created_at=row["created_at"],
        preferences=AccountPreferences.model_validate(preferences),
    )


def _cat_profile(row: Mapping[str, Any]) -> CatProfile:
    return CatProfile(
        id=row["id"],
        account_id=row["account_id"],
        name=row["name"],
        age=CatAge(value=row["age_value"], unit=AgeUnit(row["age_unit"])),
        breed=row["breed"],
        weight=CatWeight(
            value=row["weight_value"], unit=WeightUnit(row["weight_unit"])
        ),
        energy_level=EnergyLevel(row["energy_level"]),
        common_patterns=row["common_patterns"],
        known_conditions=list(row["known_conditions"]),
        photo_references=list(row["photo_references"]),
        theme=CatTheme.model_validate(row["theme"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _json_text(value: Any) -> str:
    return json.dumps(value)
