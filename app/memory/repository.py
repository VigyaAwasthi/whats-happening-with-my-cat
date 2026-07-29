"""Repository-enforced cat isolation for every memory read and write."""

from datetime import datetime, timezone
from collections.abc import Mapping
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import Field

from app.db import Database, vector_literal
from app.schemas.base import ContractModel
from app.schemas.domain import CatAge, CatProfile, CatTheme, CatWeight
from app.schemas.enums import (
    AgeUnit,
    Corner,
    EnergyLevel,
    MessageRole,
    WeightUnit,
)
from app.schemas.memory import LongTermMemory, SessionMessage


class StoredSessionMessage(ContractModel):
    """Persisted message with stable identity and timestamp."""

    id: UUID = Field(description="Message identifier.")
    role: MessageRole = Field(description="Message author role.")
    content: str = Field(description="Message content.")
    created_at: datetime = Field(description="Message creation time.")


class MemoryRepository(Protocol):
    """Every operation requires cat_id so a call site cannot omit isolation."""

    async def fetch_profile(self, cat_id: UUID) -> CatProfile | None:
        """Fetch exactly the active cat profile."""
        ...

    async def search_long_term(
        self, cat_id: UUID, embedding: list[float] | None, limit: int
    ) -> list[LongTermMemory]:
        """Fetch relevant summaries belonging only to cat_id."""
        ...

    async def ensure_session(
        self, cat_id: UUID, requested_session_id: UUID, corner: Corner
    ) -> UUID:
        """Use the requested same-cat session or create a cat-isolated replacement."""
        ...

    async def append_message(
        self,
        cat_id: UUID,
        session_id: UUID,
        role: MessageRole,
        content: str,
    ) -> None:
        """Write a message only through the composite session/cat scope."""
        ...

    async def session_messages(
        self, cat_id: UUID, session_id: UUID, corner: Corner | None = None
    ) -> list[StoredSessionMessage]:
        """Read ordered messages only for the supplied cat/session pair."""
        ...

    async def rolling_summary(
        self, cat_id: UUID, session_id: UUID, corner: Corner
    ) -> str | None:
        """Read a compacted summary only from the supplied cat/corner session."""
        ...

    async def update_rolling_summary(
        self,
        cat_id: UUID,
        session_id: UUID,
        summary: str,
        delete_message_ids: list[UUID],
    ) -> None:
        """Update and compact one cat-scoped session."""
        ...

    async def write_long_term(
        self,
        cat_id: UUID,
        source_session_id: UUID,
        summary: str,
        embedding: list[float] | None,
    ) -> LongTermMemory:
        """Persist one summary tied to a same-cat source session."""
        ...


class PostgresMemoryRepository:
    """PostgreSQL implementation with mandatory cat predicates in every statement."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def fetch_profile(self, cat_id: UUID) -> CatProfile | None:
        row = await self._database.fetch_one(
            "SELECT * FROM cat_profiles WHERE id = %s AND id = %s",
            (cat_id, cat_id),
        )
        return None if row is None else _cat_profile(row)

    async def search_long_term(
        self, cat_id: UUID, embedding: list[float] | None, limit: int
    ) -> list[LongTermMemory]:
        if embedding is None:
            rows = await self._database.fetch_all(
                """
                SELECT id, cat_id, summary, source_session_id, created_at
                FROM long_term_memory
                WHERE cat_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (cat_id, limit),
            )
        else:
            rows = await self._database.fetch_all(
                """
                SELECT id, cat_id, summary, source_session_id, created_at
                FROM long_term_memory
                WHERE cat_id = %s AND embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (cat_id, vector_literal(embedding), limit),
            )
        return [_long_term(row) for row in rows]

    async def ensure_session(
        self, cat_id: UUID, requested_session_id: UUID, corner: Corner
    ) -> UUID:
        await self._database.execute(
            """
            INSERT INTO sessions (id, cat_id, corner)
            VALUES (%s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (requested_session_id, cat_id, corner.value),
        )
        owned = await self._database.fetch_one(
            """
            SELECT id FROM sessions
            WHERE id = %s AND cat_id = %s AND corner = %s
            """,
            (requested_session_id, cat_id, corner.value),
        )
        if owned is not None:
            return requested_session_id

        replacement = uuid4()
        await self._database.execute(
            """
            INSERT INTO sessions (id, cat_id, corner)
            VALUES (%s, %s, %s)
            """,
            (replacement, cat_id, corner.value),
        )
        return replacement

    async def append_message(
        self,
        cat_id: UUID,
        session_id: UUID,
        role: MessageRole,
        content: str,
    ) -> None:
        await self._database.execute(
            """
            INSERT INTO session_messages (session_id, cat_id, role, content)
            SELECT id, cat_id, %s, %s
            FROM sessions
            WHERE id = %s AND cat_id = %s
            """,
            (role.value, content, session_id, cat_id),
        )

    async def session_messages(
        self, cat_id: UUID, session_id: UUID, corner: Corner | None = None
    ) -> list[StoredSessionMessage]:
        if corner is None:
            rows = await self._database.fetch_all(
                """
                SELECT id, role, content, created_at
                FROM session_messages
                WHERE session_id = %s AND cat_id = %s
                ORDER BY created_at, id
                """,
                (session_id, cat_id),
            )
        else:
            rows = await self._database.fetch_all(
                """
                SELECT messages.id, messages.role, messages.content,
                       messages.created_at
                FROM session_messages AS messages
                JOIN sessions
                  ON sessions.id = messages.session_id
                 AND sessions.cat_id = messages.cat_id
                WHERE messages.session_id = %s
                  AND messages.cat_id = %s
                  AND sessions.corner = %s
                ORDER BY messages.created_at, messages.id
                """,
                (session_id, cat_id, corner.value),
            )
        return [StoredSessionMessage.model_validate(row) for row in rows]

    async def rolling_summary(
        self, cat_id: UUID, session_id: UUID, corner: Corner
    ) -> str | None:
        row = await self._database.fetch_one(
            """
            SELECT rolling_summary FROM sessions
            WHERE id = %s AND cat_id = %s AND corner = %s
            """,
            (session_id, cat_id, corner.value),
        )
        return None if row is None else row["rolling_summary"]

    async def update_rolling_summary(
        self,
        cat_id: UUID,
        session_id: UUID,
        summary: str,
        delete_message_ids: list[UUID],
    ) -> None:
        async with self._database.transaction() as database:
            await database.execute(
                """
                UPDATE sessions
                SET rolling_summary = %s, updated_at = now()
                WHERE id = %s AND cat_id = %s
                """,
                (summary, session_id, cat_id),
            )
            if delete_message_ids:
                await database.execute(
                    """
                    DELETE FROM session_messages
                    WHERE session_id = %s
                      AND cat_id = %s
                      AND id = ANY(%s)
                    """,
                    (session_id, cat_id, delete_message_ids),
                )

    async def write_long_term(
        self,
        cat_id: UUID,
        source_session_id: UUID,
        summary: str,
        embedding: list[float] | None,
    ) -> LongTermMemory:
        row = await self._database.fetch_one(
            """
            INSERT INTO long_term_memory (
                cat_id, summary, source_session_id, embedding
            )
            SELECT cat_id, %s, id, %s::vector
            FROM sessions
            WHERE id = %s AND cat_id = %s
            RETURNING id, cat_id, summary, source_session_id, created_at
            """,
            (
                summary,
                None if embedding is None else vector_literal(embedding),
                source_session_id,
                cat_id,
            ),
        )
        if row is None:
            raise ValueError("cat-scoped source session does not exist")
        return _long_term(row)


class InMemoryMemoryRepository:
    """Zero-cost repository used by development and isolation tests."""

    def __init__(self) -> None:
        self.profiles: dict[UUID, CatProfile] = {}
        self.sessions: dict[tuple[UUID, UUID], Corner] = {}
        self.messages: dict[tuple[UUID, UUID], list[StoredSessionMessage]] = {}
        self.rolling_summaries: dict[tuple[UUID, UUID], str] = {}
        self.long_term: dict[UUID, list[LongTermMemory]] = {}

    async def fetch_profile(self, cat_id: UUID) -> CatProfile | None:
        return self.profiles.get(cat_id)

    async def search_long_term(
        self, cat_id: UUID, embedding: list[float] | None, limit: int
    ) -> list[LongTermMemory]:
        return list(self.long_term.get(cat_id, []))[:limit]

    async def ensure_session(
        self, cat_id: UUID, requested_session_id: UUID, corner: Corner
    ) -> UUID:
        same = (cat_id, requested_session_id)
        if self.sessions.get(same) is corner:
            return requested_session_id
        if any(session_id == requested_session_id for _, session_id in self.sessions):
            requested_session_id = uuid4()
        self.sessions[(cat_id, requested_session_id)] = corner
        self.messages.setdefault((cat_id, requested_session_id), [])
        return requested_session_id

    async def append_message(
        self,
        cat_id: UUID,
        session_id: UUID,
        role: MessageRole,
        content: str,
    ) -> None:
        key = (cat_id, session_id)
        if key not in self.sessions:
            return
        self.messages.setdefault(key, []).append(
            StoredSessionMessage(
                id=uuid4(),
                role=role,
                content=content,
                created_at=datetime.now(timezone.utc),
            )
        )

    async def session_messages(
        self, cat_id: UUID, session_id: UUID, corner: Corner | None = None
    ) -> list[StoredSessionMessage]:
        if corner is not None and self.sessions.get((cat_id, session_id)) is not corner:
            return []
        return list(self.messages.get((cat_id, session_id), []))

    async def rolling_summary(
        self, cat_id: UUID, session_id: UUID, corner: Corner
    ) -> str | None:
        if self.sessions.get((cat_id, session_id)) is not corner:
            return None
        return self.rolling_summaries.get((cat_id, session_id))

    async def update_rolling_summary(
        self,
        cat_id: UUID,
        session_id: UUID,
        summary: str,
        delete_message_ids: list[UUID],
    ) -> None:
        key = (cat_id, session_id)
        deleted = set(delete_message_ids)
        self.rolling_summaries[key] = summary
        self.messages[key] = [
            message
            for message in self.messages.get(key, [])
            if message.id not in deleted
        ]

    async def write_long_term(
        self,
        cat_id: UUID,
        source_session_id: UUID,
        summary: str,
        embedding: list[float] | None,
    ) -> LongTermMemory:
        if (cat_id, source_session_id) not in self.sessions:
            raise ValueError("cat-scoped source session does not exist")
        memory_id = uuid4()
        memory = LongTermMemory(
            id=memory_id,
            cat_id=cat_id,
            summary=summary,
            source_session_id=source_session_id,
            created_at=datetime.now(timezone.utc),
            embedding_reference=memory_id,
        )
        self.long_term.setdefault(cat_id, []).insert(0, memory)
        return memory


def profile_facts(profile: CatProfile | None) -> list[str]:
    """Render only the typed profile fields allowed into AI context."""
    if profile is None:
        return []
    facts = [
        f"name: {profile.name}",
        f"age: {profile.age.value:g} {profile.age.unit.value}",
        f"energy level: {profile.energy_level.value}/5",
    ]
    if profile.breed:
        facts.append(f"breed: {profile.breed}")
    if profile.known_conditions:
        facts.append("known conditions: " + ", ".join(profile.known_conditions))
    if profile.common_patterns:
        facts.append("common patterns: " + profile.common_patterns)
    return facts


def _long_term(row: Mapping[str, Any]) -> LongTermMemory:
    return LongTermMemory.model_validate(
        {
            **row,
            "embedding_reference": row["id"],
        }
    )


def _cat_profile(row: Mapping[str, Any]) -> CatProfile:
    return CatProfile(
        id=row["id"],
        account_id=row["account_id"],
        name=str(row["name"]),
        age=CatAge(value=row["age_value"], unit=AgeUnit(row["age_unit"])),
        breed=None if row["breed"] is None else str(row["breed"]),
        weight=CatWeight(
            value=row["weight_value"], unit=WeightUnit(row["weight_unit"])
        ),
        energy_level=EnergyLevel(row["energy_level"]),
        common_patterns=str(row["common_patterns"]),
        known_conditions=list(row["known_conditions"]),
        photo_references=list(row["photo_references"]),
        theme=CatTheme.model_validate(row["theme"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
