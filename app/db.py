"""Small async PostgreSQL boundary shared by repositories."""

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol, cast

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


Params = Sequence[Any] | Mapping[str, Any] | None
Row = dict[str, Any]


class Database(Protocol):
    """Minimal mockable database interface; SQL remains explicit in repositories."""

    async def fetch_all(self, query: str, params: Params = None) -> list[Row]:
        """Return all matching rows."""
        ...

    async def fetch_one(self, query: str, params: Params = None) -> Row | None:
        """Return one matching row or null."""
        ...

    async def execute(self, query: str, params: Params = None) -> int:
        """Execute a mutation and return the affected row count."""
        ...

    def transaction(self) -> AbstractAsyncContextManager["Database"]:
        """Yield an atomic database view."""
        ...


class PostgresDatabase:
    """psycopg connection-pool adapter with no domain decisions."""

    def __init__(self, database_url: str, *, min_size: int = 1, max_size: int = 10):
        self._pool = cast(
            AsyncConnectionPool[AsyncConnection[Row]],
            AsyncConnectionPool(
                conninfo=database_url,
                min_size=min_size,
                max_size=max_size,
                kwargs={"row_factory": dict_row},
                open=False,
            ),
        )
        self._connection: AsyncConnection[Row] | None = None

    async def open(self) -> None:
        """Open the connection pool."""
        await self._pool.open()

    async def close(self) -> None:
        """Close the connection pool."""
        await self._pool.close()

    async def fetch_all(self, query: str, params: Params = None) -> list[Row]:
        async with self._cursor() as cursor:
            await cursor.execute(query, params)
            return list(await cursor.fetchall())

    async def fetch_one(self, query: str, params: Params = None) -> Row | None:
        async with self._cursor() as cursor:
            await cursor.execute(query, params)
            row = await cursor.fetchone()
            return dict(row) if row is not None else None

    async def execute(self, query: str, params: Params = None) -> int:
        async with self._cursor() as cursor:
            await cursor.execute(query, params)
            return cursor.rowcount

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["PostgresDatabase"]:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                transactional = PostgresDatabase.__new__(PostgresDatabase)
                transactional._pool = self._pool
                transactional._connection = connection
                yield transactional

    @asynccontextmanager
    async def _cursor(self) -> AsyncIterator[Any]:
        if self._connection is not None:
            async with self._connection.cursor() as cursor:
                yield cursor
            return
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                yield cursor


def vector_literal(values: Sequence[float]) -> str:
    """Encode a validated numeric vector for an explicit ``::vector`` SQL cast."""
    return "[" + ",".join(format(float(value), ".9g") for value in values) + "]"
