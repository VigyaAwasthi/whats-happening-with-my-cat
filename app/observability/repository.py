"""Persistence for generation traces.

The governing rule, from the hard requirements: **writing a trace must never
fail a user request.** Every public method here is safe to call and swallows its
own failures after logging. Callers do not need a try/except, and must not gate
the response on the result.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID

from app.db import Database
from app.schemas.trace import GenerationTrace

logger = logging.getLogger(__name__)


async def write_trace_safely(
    repository: "TraceRepository", trace: GenerationTrace
) -> bool:
    """Persist a trace under the guarantee that nothing here can fail a request.

    `PostgresTraceRepository.write` already swallows its own errors, but that is
    one implementation's promise. This wrapper makes the guarantee structural:
    whatever repository is installed — a future backend, a test double, a
    misbehaving stub — a raise here cannot escape into the answer path. The call
    sites in both orchestrators go through this, never through `write` directly.
    """
    try:
        return await repository.write(trace)
    except Exception:
        logger.exception(
            "generation trace write raised; answer unaffected generation_id=%s",
            trace.generation_id,
        )
        return False


class TraceRepository(Protocol):
    """Stores generation traces without ever propagating a failure."""

    async def write(self, trace: GenerationTrace) -> bool:
        """Persist one trace. Returns success; never raises."""
        ...

    async def get(self, generation_id: UUID) -> GenerationTrace | None:
        """Read one trace back, or None."""
        ...

    async def for_cats(self, cat_ids: list[UUID]) -> list[GenerationTrace]:
        """Every trace for the given cats, for account export."""
        ...

    async def prune(self, older_than_days: int) -> int:
        """Delete traces past the retention window; returns rows removed."""
        ...


class InMemoryTraceRepository:
    """Development and test store with the same never-raises contract."""

    def __init__(self) -> None:
        self.traces: dict[UUID, GenerationTrace] = {}

    async def write(self, trace: GenerationTrace) -> bool:
        self.traces[trace.generation_id] = trace
        return True

    async def get(self, generation_id: UUID) -> GenerationTrace | None:
        return self.traces.get(generation_id)

    async def for_cats(self, cat_ids: list[UUID]) -> list[GenerationTrace]:
        wanted = set(cat_ids)
        return sorted(
            (trace for trace in self.traces.values() if trace.cat_id in wanted),
            key=lambda trace: trace.created_at,
        )

    async def prune(self, older_than_days: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        stale = [
            generation_id
            for generation_id, trace in self.traces.items()
            if trace.created_at < cutoff
        ]
        for generation_id in stale:
            del self.traces[generation_id]
        return len(stale)


class PostgresTraceRepository:
    """Durable trace storage. Failures are logged and swallowed, by contract."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def write(self, trace: GenerationTrace) -> bool:
        try:
            await self._database.execute(
                """
                INSERT INTO generation_traces (
                    generation_id, cat_id, session_id, corner, created_at,
                    query, response_text, retrieval, consensus,
                    answer_mode, response_kind, model_calls, prompt_version,
                    model_call_count, total_input_tokens, total_output_tokens,
                    cache_read_tokens, cache_write_tokens, cost_usd, latency,
                    groundedness, red_flag_fired, red_flag_rules, canned_response_id
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s::jsonb, %s::jsonb,
                    %s, %s, %s::jsonb, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s::jsonb,
                    %s, %s, %s::jsonb, %s
                )
                ON CONFLICT (generation_id) DO NOTHING
                """,
                (
                    trace.generation_id,
                    trace.cat_id,
                    trace.session_id,
                    trace.corner.value,
                    trace.created_at,
                    trace.query,
                    trace.response_text,
                    json.dumps([row.model_dump(mode="json") for row in trace.retrieval]),
                    json.dumps(trace.consensus.model_dump(mode="json")),
                    trace.answer_mode,
                    trace.response_kind,
                    json.dumps([call.model_dump(mode="json") for call in trace.model_calls]),
                    trace.prompt_version,
                    trace.model_call_count,
                    trace.total_input_tokens,
                    trace.total_output_tokens,
                    trace.cache_read_tokens,
                    trace.cache_write_tokens,
                    trace.cost_usd,
                    json.dumps(trace.latency.model_dump(mode="json")),
                    trace.groundedness.value,
                    trace.red_flag_fired,
                    json.dumps(trace.red_flag_rules),
                    trace.canned_response_id,
                ),
            )
            return True
        except Exception:
            # Deliberately swallowed. Losing observability is strictly better
            # than failing an answer the user is waiting for.
            logger.exception(
                "generation trace write failed; answer unaffected generation_id=%s",
                trace.generation_id,
            )
            return False

    async def get(self, generation_id: UUID) -> GenerationTrace | None:
        try:
            row = await self._database.fetch_one(
                "SELECT * FROM generation_traces WHERE generation_id = %s",
                (generation_id,),
            )
            return None if row is None else _to_trace(row)
        except Exception:
            logger.exception("generation trace read failed")
            return None

    async def for_cats(self, cat_ids: list[UUID]) -> list[GenerationTrace]:
        if not cat_ids:
            return []
        try:
            rows = await self._database.fetch_all(
                "SELECT * FROM generation_traces WHERE cat_id = ANY(%s)"
                " ORDER BY created_at",
                (list(cat_ids),),
            )
            return [_to_trace(row) for row in rows]
        except Exception:
            logger.exception("generation trace export read failed")
            return []

    async def prune(self, older_than_days: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        return await self._database.execute(
            "DELETE FROM generation_traces WHERE created_at < %s", (cutoff,)
        )


def _to_trace(row: dict[str, Any]) -> GenerationTrace:
    """Rebuild the typed record from a row, tolerating text-or-jsonb columns."""

    def decode(value: Any, fallback: Any) -> Any:
        if value is None:
            return fallback
        return json.loads(value) if isinstance(value, str) else value

    return GenerationTrace.model_validate(
        {
            "generation_id": row["generation_id"],
            "cat_id": row["cat_id"],
            "session_id": row["session_id"],
            "corner": row["corner"],
            "created_at": row["created_at"],
            "query": row.get("query") or "",
            "response_text": row.get("response_text") or "",
            "retrieval": decode(row.get("retrieval"), []),
            "consensus": decode(row.get("consensus"), {}),
            "answer_mode": row.get("answer_mode"),
            "response_kind": row.get("response_kind"),
            "model_calls": decode(row.get("model_calls"), []),
            "prompt_version": row.get("prompt_version") or "v1",
            "model_call_count": row.get("model_call_count") or 0,
            "total_input_tokens": row.get("total_input_tokens") or 0,
            "total_output_tokens": row.get("total_output_tokens") or 0,
            "cache_read_tokens": row.get("cache_read_tokens") or 0,
            "cache_write_tokens": row.get("cache_write_tokens") or 0,
            "cost_usd": float(row.get("cost_usd") or 0),
            "latency": decode(row.get("latency"), {}),
            "groundedness": row.get("groundedness") or "not_applicable",
            "red_flag_fired": bool(row.get("red_flag_fired")),
            "red_flag_rules": decode(row.get("red_flag_rules"), []),
            "canned_response_id": row.get("canned_response_id"),
        }
    )
