"""Generation tracing and generation-scoped feedback contracts.

The hard requirements this file exists to hold:

1. A trace-persistence failure must never fail a user request.
2. Traces are covered by account export and by the delete cascade.
3. An emergency answer records zero model calls, proving the deterministic gate
   short-circuited before any model was consulted.
"""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.container import get_services
from app.main import app
from app.observability.collector import TraceCollector, trace_scope
from app.observability.repository import InMemoryTraceRepository
from app.schemas.api import FeedbackRequest
from app.schemas.enums import Corner, FeedbackReason, FeedbackThumb
from app.schemas.trace import GroundednessOutcome, RetrievalStage


def _cat_payload(cat_id: UUID, name: str = "Mochi") -> dict[str, object]:
    return {
        "cat_id": str(cat_id),
        "name": name,
        "age": {"value": 3, "unit": "years"},
        "breed": None,
        "sex": "unknown",
        "weight": {"value": 9, "unit": "lb"},
        "energy_level": 3,
        "common_patterns": "Knocks pens off the desk.",
        "known_conditions": [],
        "photo_references": [],
        "theme": {"primary_color": "#112233", "accent_color": "#AABBCC"},
    }


def _behavior(client: TestClient, cat_id: UUID, message: str) -> dict:
    response = client.post(
        "/chat/behavior",
        headers={"X-Active-Cat-ID": str(cat_id)},
        json={
            "cat_id": str(cat_id),
            "message": message,
            "session_id": str(uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _health(client: TestClient, cat_id: UUID, message: str) -> dict:
    response = client.post(
        "/chat/health",
        headers={"X-Active-Cat-ID": str(cat_id)},
        json={
            "cat_id": str(cat_id),
            "message": message,
            "intake": None,
            "session_id": str(uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------
# Part A — a generation id reaches the client and a trace lands behind it
# --------------------------------------------------------------------------


def test_both_corners_return_a_generation_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNTIME_MODE", "development")
    cat_id = uuid4()
    with TestClient(app) as client:
        assert client.post("/cats", json=_cat_payload(cat_id)).status_code == 201
        behavior = _behavior(client, cat_id, "Why does my cat knock things off tables?")
        health = _health(client, cat_id, "She has been sneezing a little")
        traces = get_services().traces
        assert traces is not None
        for payload in (behavior, health):
            assert payload["generation_id"], "every answer must be identifiable"
            trace = await_sync(traces.get(UUID(payload["generation_id"])))
            assert trace is not None, "a returned generation_id must have a trace"
            assert trace.cat_id == cat_id


def test_the_trace_captures_what_a_thumbs_down_needs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: enough to tell retrieval failure from generation failure."""
    monkeypatch.setenv("RUNTIME_MODE", "development")
    cat_id = uuid4()
    query = "Why does my cat knock things off tables?"
    with TestClient(app) as client:
        assert client.post("/cats", json=_cat_payload(cat_id)).status_code == 201
        payload = _behavior(client, cat_id, query)
        traces = get_services().traces
        assert traces is not None
        trace = await_sync(traces.get(UUID(payload["generation_id"])))

    assert trace is not None
    assert trace.query == query, "the query as sent"
    assert trace.response_text, "the answer as served"
    assert trace.corner is Corner.BEHAVIOR
    assert trace.answer_mode is not None, "which mode produced the answer"
    assert trace.prompt_version, "which prompt produced it"
    assert trace.model_calls, "which models were called"
    assert all(call.model for call in trace.model_calls)
    assert trace.latency.total_ms > 0
    # Retrieval detail is the single most valuable diagnostic field.
    assert trace.entries_at(RetrievalStage.HYBRID_CANDIDATES), "candidates recorded"


def test_retrieval_is_recorded_per_stage_not_only_at_the_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Never a candidate" and "demoted by the reranker" need different fixes."""
    monkeypatch.setenv("RUNTIME_MODE", "development")
    cat_id = uuid4()
    with TestClient(app) as client:
        assert client.post("/cats", json=_cat_payload(cat_id)).status_code == 201
        payload = _behavior(client, cat_id, "Why does my cat knead blankets?")
        traces = get_services().traces
        assert traces is not None
        trace = await_sync(traces.get(UUID(payload["generation_id"])))

    assert trace is not None
    candidates = trace.entries_at(RetrievalStage.HYBRID_CANDIDATES)
    final = trace.entries_at(RetrievalStage.FINAL_CONTEXT)
    assert candidates, "hybrid candidates must be recorded"
    # The final context is always a subset of what was retrieved; if it were
    # not, the trace would be describing an answer that saw unretrieved text.
    assert set(final).issubset(set(candidates))
    for row in trace.retrieval:
        assert row.entry_id
        assert row.rank >= 0


def test_consensus_explains_the_mode_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNTIME_MODE", "development")
    cat_id = uuid4()
    with TestClient(app) as client:
        assert client.post("/cats", json=_cat_payload(cat_id)).status_code == 201
        payload = _behavior(client, cat_id, "Why does my cat knock things off tables?")
        traces = get_services().traces
        assert traces is not None
        trace = await_sync(traces.get(UUID(payload["generation_id"])))

    assert trace is not None
    # Mode selection turns on signal agreement, so the trace must say which
    # signals agreed — otherwise "why general_knowledge?" is unanswerable.
    assert isinstance(trace.consensus.semantic_agrees, bool)
    assert isinstance(trace.consensus.lexical_agrees, bool)
    if trace.answer_mode == "corpus_grounded":
        assert trace.consensus.top_entry_id is not None


# --------------------------------------------------------------------------
# Hard requirement 3 — the emergency gate records zero model calls
# --------------------------------------------------------------------------


def test_emergency_answer_records_zero_model_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auditable proof that no model was consulted on an emergency."""
    monkeypatch.setenv("RUNTIME_MODE", "development")
    cat_id = uuid4()
    with TestClient(app) as client:
        assert client.post("/cats", json=_cat_payload(cat_id)).status_code == 201
        payload = _health(
            client, cat_id, "My cat can't pee and keeps straining in the litter box"
        )
        assert payload["result"]["severity"] == "emergency"
        traces = get_services().traces
        assert traces is not None
        trace = await_sync(traces.get(UUID(payload["generation_id"])))

    assert trace is not None
    assert trace.red_flag_fired is True
    assert trace.red_flag_rules, "the matched rule must be recorded, not just the fact"
    assert trace.model_call_count == 0
    assert trace.model_calls == []


# --------------------------------------------------------------------------
# Hard requirement 1 — a trace failure never fails a request
# --------------------------------------------------------------------------


async def test_a_failing_trace_write_does_not_fail_the_answer() -> None:
    """The governing rule for all of this: observability is never load-bearing."""

    class ExplodingTraceRepository(InMemoryTraceRepository):
        async def write(self, trace):  # type: ignore[no-untyped-def]
            raise RuntimeError("trace storage is down")

    from app.orchestration.behavior import BehaviorOrchestrator

    # Reuse the development graph, swapping in a repository that always fails.
    import os

    os.environ["RUNTIME_MODE"] = "development"
    with TestClient(app) as client:
        cat_id = uuid4()
        assert client.post("/cats", json=_cat_payload(cat_id)).status_code == 201
        services = get_services()
        assert isinstance(services.behavior, BehaviorOrchestrator)
        services.behavior._traces = ExplodingTraceRepository()  # type: ignore[attr-defined]
        services.health._traces = ExplodingTraceRepository()  # type: ignore[attr-defined]

        behavior = client.post(
            "/chat/behavior",
            headers={"X-Active-Cat-ID": str(cat_id)},
            json={
                "cat_id": str(cat_id),
                "message": "Why does my cat sit in boxes?",
                "session_id": str(uuid4()),
            },
        )
        health = client.post(
            "/chat/health",
            headers={"X-Active-Cat-ID": str(cat_id)},
            json={
                "cat_id": str(cat_id),
                "message": "She seems a bit quiet today",
                "intake": None,
                "session_id": str(uuid4()),
            },
        )

    assert behavior.status_code == 200, "a trace fault must not fail the answer"
    assert health.status_code == 200
    assert behavior.json()["result"]["interpretation"]


async def test_the_postgres_repository_swallows_write_failures() -> None:
    """`write` returns False rather than raising, so callers cannot be broken."""
    from app.observability.repository import PostgresTraceRepository

    class BrokenDatabase:
        async def execute(self, query: str, params: object = None) -> int:
            raise RuntimeError("connection reset")

        async def fetch_one(self, query: str, params: object = None) -> None:
            raise RuntimeError("connection reset")

        async def fetch_all(self, query: str, params: object = None) -> list:
            raise RuntimeError("connection reset")

        def transaction(self):  # pragma: no cover - unused here
            raise NotImplementedError

    repository = PostgresTraceRepository(BrokenDatabase())  # type: ignore[arg-type]
    collector = TraceCollector(
        cat_id=uuid4(), session_id=uuid4(), corner=Corner.BEHAVIOR, query="hello"
    )
    assert await repository.write(collector.build()) is False
    assert await repository.get(uuid4()) is None
    assert await repository.for_cats([uuid4()]) == []


# --------------------------------------------------------------------------
# Hard requirement 2 — export and delete cover traces
# --------------------------------------------------------------------------


def test_traces_are_included_in_account_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNTIME_MODE", "development")
    cat_id = uuid4()
    with TestClient(app) as client:
        assert client.post("/cats", json=_cat_payload(cat_id)).status_code == 201
        payload = _behavior(client, cat_id, "Why does my cat chirp at birds?")
        export = client.get("/account/export")
        assert export.status_code == 200
        exported = export.json()

    ids = {trace["generation_id"] for trace in exported["generation_traces"]}
    assert payload["generation_id"] in ids, "the user's own queries must be exportable"


def test_account_deletion_removes_traces(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNTIME_MODE", "development")
    cat_id = uuid4()
    with TestClient(app) as client:
        assert client.post("/cats", json=_cat_payload(cat_id)).status_code == 201
        payload = _behavior(client, cat_id, "Why does my cat follow me around?")
        generation_id = UUID(payload["generation_id"])
        traces = get_services().traces
        assert traces is not None
        assert await_sync(traces.get(generation_id)) is not None

        assert client.delete("/account").status_code == 200
        assert await_sync(traces.get(generation_id)) is None, (
            "a deleted account must not leave the user's queries behind"
        )


async def test_retention_prunes_only_expired_traces() -> None:
    from datetime import timedelta

    repository = InMemoryTraceRepository()
    collector = TraceCollector(
        cat_id=uuid4(), session_id=uuid4(), corner=Corner.BEHAVIOR, query="q"
    )
    fresh = collector.build()
    stale = fresh.model_copy(
        update={
            "generation_id": uuid4(),
            "created_at": fresh.created_at - timedelta(days=120),
        }
    )
    await repository.write(fresh)
    await repository.write(stale)

    assert await repository.prune(90) == 1
    assert await repository.get(fresh.generation_id) is not None
    assert await repository.get(stale.generation_id) is None


# --------------------------------------------------------------------------
# Part B — feedback
# --------------------------------------------------------------------------


def test_feedback_references_the_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNTIME_MODE", "development")
    cat_id = uuid4()
    with TestClient(app) as client:
        assert client.post("/cats", json=_cat_payload(cat_id)).status_code == 201
        payload = _behavior(client, cat_id, "Why does my cat stare at walls?")
        response = client.post(
            "/feedback",
            json={
                "cat_id": str(cat_id),
                "session_id": payload["session_id"],
                "corner": "behavior",
                "thumb": "down",
                "generation_id": payload["generation_id"],
                "reason": "not_specific_to_my_cat",
                "reason_text": "It described cats in general, not mine.",
            },
        )
    assert response.status_code == 201, response.text
    record = response.json()["feedback"]
    assert record["generation_id"] == payload["generation_id"]
    assert record["reason"] == "not_specific_to_my_cat"
    assert record["reason_text"]


def test_feedback_is_editable_in_place(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-rating one answer must not leave two contradictory ratings behind."""
    monkeypatch.setenv("RUNTIME_MODE", "development")
    cat_id = uuid4()
    with TestClient(app) as client:
        assert client.post("/cats", json=_cat_payload(cat_id)).status_code == 201
        payload = _behavior(client, cat_id, "Why does my cat sleep so much?")
        body = {
            "cat_id": str(cat_id),
            "session_id": payload["session_id"],
            "corner": "behavior",
            "thumb": "down",
            "generation_id": payload["generation_id"],
            "reason": "too_cautious",
        }
        first = client.post("/feedback", json=body)
        assert first.status_code == 201
        revised = client.post(
            "/feedback", json={**body, "thumb": "up", "reason": None}
        )
        assert revised.status_code == 201
        export = client.get("/account/export").json()

    for_generation = [
        item
        for item in export["feedback"]
        if item["generation_id"] == payload["generation_id"]
    ]
    assert len(for_generation) == 1, "editing must replace, not append"
    assert for_generation[0]["thumb"] == "up"
    assert for_generation[0]["reason"] is None


def test_feedback_is_revocable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNTIME_MODE", "development")
    cat_id = uuid4()
    with TestClient(app) as client:
        assert client.post("/cats", json=_cat_payload(cat_id)).status_code == 201
        payload = _behavior(client, cat_id, "Why does my cat bite gently?")
        created = client.post(
            "/feedback",
            json={
                "cat_id": str(cat_id),
                "session_id": payload["session_id"],
                "corner": "behavior",
                "thumb": "down",
                "generation_id": payload["generation_id"],
                "reason": "wrong_information",
            },
        )
        feedback_id = created.json()["feedback"]["id"]
        revoked = client.delete(
            f"/feedback?cat_id={cat_id}&feedback_id={feedback_id}"
        )
        assert revoked.status_code == 200
        export = client.get("/account/export").json()

    assert all(item["id"] != feedback_id for item in export["feedback"])


def test_feedback_for_another_cats_generation_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNTIME_MODE", "development")
    cat_a, cat_b = uuid4(), uuid4()
    with TestClient(app) as client:
        assert client.post("/cats", json=_cat_payload(cat_a, "Mochi")).status_code == 201
        assert client.post("/cats", json=_cat_payload(cat_b, "Pepper")).status_code == 201
        payload = _behavior(client, cat_a, "Why does my cat purr?")
        response = client.post(
            "/feedback",
            json={
                "cat_id": str(cat_b),
                "session_id": payload["session_id"],
                "corner": "behavior",
                "thumb": "down",
                "generation_id": payload["generation_id"],
            },
        )
    assert response.status_code == 403
    assert response.json()["code"] == "CAT_SCOPE_MISMATCH"


def test_feedback_survives_a_missing_trace_as_untraceable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed or pruned trace must not also discard the user's rating."""
    monkeypatch.setenv("RUNTIME_MODE", "development")
    cat_id = uuid4()

    with TestClient(app) as client:
        assert client.post("/cats", json=_cat_payload(cat_id)).status_code == 201
        payload = _behavior(client, cat_id, "Why does my cat chatter at birds?")
        generation_id = UUID(payload["generation_id"])

        traces = get_services().traces
        assert isinstance(traces, InMemoryTraceRepository)
        traces.traces.pop(generation_id)

        response = client.post(
            "/feedback",
            json={
                "cat_id": str(cat_id),
                "session_id": payload["session_id"],
                "corner": "behavior",
                "thumb": "down",
                "generation_id": str(generation_id),
                "reason": "did_not_answer",
            },
        )
        assert response.status_code == 201, response.text
        feedback = response.json()["feedback"]
        export = client.get("/account/export").json()

    assert feedback["generation_id"] is None
    assert any(
        item["id"] == feedback["id"] and item["generation_id"] is None
        for item in export["feedback"]
    )


def test_a_reason_on_positive_feedback_is_rejected() -> None:
    """A structured reason explains a complaint; on a thumbs-up it is noise."""
    with pytest.raises(ValidationError, match="only to negative feedback"):
        FeedbackRequest(
            cat_id=uuid4(),
            session_id=uuid4(),
            corner=Corner.BEHAVIOR,
            thumb=FeedbackThumb.UP,
            reason=FeedbackReason.TOO_CAUTIOUS,
        )


def test_every_reason_maps_to_a_distinct_fix() -> None:
    """The reason list is a diagnostic instrument, not a satisfaction survey."""
    assert {reason.value for reason in FeedbackReason} == {
        "wrong_information",
        "not_specific_to_my_cat",
        "did_not_answer",
        "too_cautious",
        "other",
    }


# --------------------------------------------------------------------------
# Collector unit behavior
# --------------------------------------------------------------------------


def test_collector_totals_are_summed_from_the_calls() -> None:
    from app.schemas.trace import ModelCallTrace

    collector = TraceCollector(
        cat_id=uuid4(), session_id=uuid4(), corner=Corner.HEALTH, query="q"
    )
    with trace_scope(collector):
        for tokens in (100, 250):
            collector.record_model_call(
                ModelCallTrace(
                    purpose="fast",
                    model="m",
                    prompt_version="v1",
                    input_tokens=tokens,
                    output_tokens=10,
                    cache_read_tokens=5,
                    cache_write_tokens=1,
                    cost_usd=0.001,
                )
            )
        collector.set_groundedness(GroundednessOutcome.CLAIMS_STRIPPED)
    trace = collector.build()

    assert trace.total_input_tokens == 350
    assert trace.total_output_tokens == 20
    assert trace.cache_read_tokens == 10
    assert trace.cache_write_tokens == 2
    assert trace.cost_usd == pytest.approx(0.002)
    assert trace.model_call_count == 2
    assert trace.groundedness is GroundednessOutcome.CLAIMS_STRIPPED


def test_collector_outside_a_scope_is_a_no_op() -> None:
    """Code paths that run without a trace must not crash on recording."""
    from app.observability.recording import (
        record_consensus,
        record_final_context,
        record_retrieval_stages,
    )

    record_retrieval_stages([])
    record_final_context([])
    record_consensus([])


def await_sync(awaitable):  # type: ignore[no-untyped-def]
    """Run a coroutine from a sync test body."""
    import asyncio

    return asyncio.new_event_loop().run_until_complete(awaitable)
