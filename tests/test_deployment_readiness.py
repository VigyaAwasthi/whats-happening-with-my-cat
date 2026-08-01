"""Deployment-hardening contracts: probes, typed edges, spend window, config guards.

Each test here pins one property the deployment depends on and that is easy to
regress silently: a probe that starts requiring auth, an error handler that
starts echoing user input, a spend cap that stops resetting.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.rate_limit import AccountRateLimiter
from app.errors import APIErrorCode
from app.llm.client import (
    InMemorySpendLedger,
    SpendTracker,
    TokenPricing,
    spend_budget_key,
)
from app.logging_config import redact
from app.main import app
from app.runtime_config import REVIEWED_ANTHROPIC_MODELS, RuntimeSettings
from app.schemas.api import AuthSessionResponse
from app.schemas.enums import AuthStatus


# --------------------------------------------------------------------------
# B1 — liveness and readiness
# --------------------------------------------------------------------------


def test_health_and_ready_need_no_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNTIME_MODE", "development")
    with TestClient(app) as client:
        health = client.get("/health")
        ready = client.get("/ready")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


def test_probes_do_not_leak_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """An anonymous caller must not learn hostnames, model ids, or key names."""
    monkeypatch.setenv("RUNTIME_MODE", "development")
    with TestClient(app) as client:
        bodies = client.get("/health").text + client.get("/ready").text
    lowered = bodies.casefold()
    for leak in (
        "supabase",
        "postgres",
        "anthropic",
        "claude",
        "voyage",
        "key",
        "token",
        "url",
        "password",
        "cap",
    ):
        assert leak not in lowered, f"probe response leaked {leak!r}"


def test_ready_reports_only_coarse_subsystem_booleans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNTIME_MODE", "development")
    with TestClient(app) as client:
        payload = client.get("/ready").json()
    assert set(payload) == {"status", "checks"}
    assert set(payload["checks"]) == {"database", "configuration"}
    assert all(isinstance(value, bool) for value in payload["checks"].values())


# --------------------------------------------------------------------------
# B3 — no untyped error, no stack trace, no echoed user content
# --------------------------------------------------------------------------


def test_unknown_route_returns_the_typed_error_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNTIME_MODE", "development")
    with TestClient(app) as client:
        response = client.get("/no-such-route")
    assert response.status_code == 404
    assert response.json() == {
        "code": APIErrorCode.NOT_FOUND.value,
        "message": "The requested resource does not exist.",
        "retryable": False,
    }


def test_validation_failure_never_echoes_the_user_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FastAPI's default 422 reflects the rejected value; ours must not.

    On these routes the rejected value is the user's chat message, so the
    default behavior would write user content into an error body.
    """
    monkeypatch.setenv("RUNTIME_MODE", "development")
    secret_text = "my cat swallowed a needle at 3am and I am panicking"
    cat_id = uuid4()
    with TestClient(app) as client:
        # The active-cat guard runs before body validation, so the request has
        # to be authorized to reach the validation path at all.
        assert client.post("/cats", json=_cat_payload(cat_id)).status_code == 201
        response = client.post(
            "/chat/behavior",
            headers={"X-Active-Cat-ID": str(cat_id)},
            json={"cat_id": str(cat_id), "message": secret_text},  # no session_id
        )
    assert response.status_code == 422
    assert secret_text not in response.text
    assert response.json()["code"] == APIErrorCode.INVALID_REQUEST.value


def _cat_payload(cat_id: object) -> dict[str, object]:
    return {
        "cat_id": str(cat_id),
        "name": "Mochi",
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


def test_no_stack_trace_reaches_an_unauthenticated_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNTIME_MODE", "development")
    with TestClient(app) as client:
        bodies = [
            client.get("/no-such-route").text,
            client.post("/auth/sign-in", json={"nope": 1}).text,
            client.get("/cats").text,
        ]
    for body in bodies:
        assert "Traceback" not in body
        assert "File \"" not in body
        assert "app/" not in body


# --------------------------------------------------------------------------
# B4 — per-account chat rate limiting
# --------------------------------------------------------------------------


def test_rate_limiter_admits_the_budget_then_refuses() -> None:
    limiter = AccountRateLimiter(limit_per_minute=3)
    account = uuid4()
    assert [limiter.check(account, now=100.0) for _ in range(3)] == [None, None, None]
    retry_after = limiter.check(account, now=100.0)
    assert retry_after is not None and 0 < retry_after <= 60


def test_rate_limiter_is_scoped_per_account() -> None:
    """One noisy account must not spend another account's budget."""
    limiter = AccountRateLimiter(limit_per_minute=1)
    noisy, quiet = uuid4(), uuid4()
    assert limiter.check(noisy, now=10.0) is None
    assert limiter.check(noisy, now=10.0) is not None
    assert limiter.check(quiet, now=10.0) is None


def test_rate_limiter_window_rolls_forward() -> None:
    limiter = AccountRateLimiter(limit_per_minute=1)
    account = uuid4()
    assert limiter.check(account, now=0.0) is None
    assert limiter.check(account, now=30.0) is not None
    assert limiter.check(account, now=61.0) is None


# --------------------------------------------------------------------------
# A3 — the spend cap survives restart and does not disable the system forever
# --------------------------------------------------------------------------


def test_monthly_window_key_changes_at_the_utc_month_boundary() -> None:
    last_moment = datetime(2026, 7, 31, 23, 59, 59, tzinfo=timezone.utc)
    first_moment = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert spend_budget_key("monthly", now=last_moment) == "global:2026-07"
    assert spend_budget_key("monthly", now=first_moment) == "global:2026-08"
    assert spend_budget_key("lifetime", now=first_moment) == "global"


async def test_reaching_the_cap_does_not_disable_the_next_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cap reached in one UTC month must not block the next month."""
    ledger = InMemorySpendLedger()
    pricing = {
        "m": TokenPricing(
            input_per_million_usd=Decimal("1000000"),
            output_per_million_usd=Decimal("1000000"),
            cache_write_per_million_usd=Decimal("1000000"),
            cache_read_per_million_usd=Decimal("1000000"),
        )
    }

    key_july = spend_budget_key(
        "monthly",
        now=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )
    key_august = spend_budget_key(
        "monthly",
        now=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )
    assert key_july != key_august

    # Pin the tracker to July and exhaust that month's allowance.
    monkeypatch.setattr(
        SpendTracker,
        "budget_key",
        property(lambda _tracker: key_july),
    )
    july = SpendTracker(
        cap_usd=Decimal("1"),
        pricing=pricing,
        ledger=ledger,
        window="monthly",
    )
    assert await july.reserve("m", 1, 0) is not None
    assert await july.reserve("m", 1, 0) is None
    assert await ledger.total(key_july) == Decimal("1")

    # Simulate a fresh process after the UTC month boundary.
    monkeypatch.setattr(
        SpendTracker,
        "budget_key",
        property(lambda _tracker: key_august),
    )
    august = SpendTracker(
        cap_usd=Decimal("1"),
        pricing=pricing,
        ledger=ledger,
        window="monthly",
    )
    assert await august.current_spend() == Decimal("0")
    assert await august.reserve("m", 1, 0) is not None
    assert await ledger.total(key_august) == Decimal("1")


async def test_spend_survives_a_restart_because_the_ledger_is_shared() -> None:
    ledger = InMemorySpendLedger()
    pricing = {
        "m": TokenPricing(
            input_per_million_usd=Decimal("1000000"),
            output_per_million_usd=Decimal("0"),
            cache_write_per_million_usd=Decimal("0"),
            cache_read_per_million_usd=Decimal("0"),
        )
    }
    before = SpendTracker(cap_usd=Decimal("1"), pricing=pricing, ledger=ledger)
    assert await before.reserve("m", 1, 0) is not None

    after_restart = SpendTracker(cap_usd=Decimal("1"), pricing=pricing, ledger=ledger)
    assert await after_restart.current_spend() == Decimal("1")
    assert await after_restart.reserve("m", 1, 0) is None


async def test_reservation_reconciles_against_the_window_that_was_debited() -> None:
    """A window boundary crossed mid-call must not corrupt either window."""
    ledger = InMemorySpendLedger()
    pricing = {
        "m": TokenPricing(
            input_per_million_usd=Decimal("1000000"),
            output_per_million_usd=Decimal("0"),
            cache_write_per_million_usd=Decimal("0"),
            cache_read_per_million_usd=Decimal("0"),
        )
    }
    tracker = SpendTracker(
        cap_usd=Decimal("10"), pricing=pricing, ledger=ledger, window="monthly"
    )
    reservation = await tracker.reserve("m", 2, 0)
    assert reservation is not None
    assert reservation.budget_key == spend_budget_key("monthly")
    await tracker.reconcile(
        "m",
        reservation,
        actual_input=1,
        actual_output=0,
        cache_write_input=0,
        cache_read_input=0,
    )
    assert await ledger.total(reservation.budget_key) == Decimal("1")


async def test_approaching_cap_warns_before_calls_start_failing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pricing = {
        "m": TokenPricing(
            input_per_million_usd=Decimal("1000000"),
            output_per_million_usd=Decimal("0"),
            cache_write_per_million_usd=Decimal("0"),
            cache_read_per_million_usd=Decimal("0"),
        )
    }
    tracker = SpendTracker(
        cap_usd=Decimal("10"), pricing=pricing, warning_ratio=0.8
    )
    with caplog.at_level("WARNING"):
        await tracker.reserve("m", 7, 0)  # 70% — below the threshold
        assert "llm_spend_approaching_cap" not in caplog.text
        await tracker.reserve("m", 2, 0)  # 90% — crosses it, still under cap
        assert "llm_spend_approaching_cap" in caplog.text


# --------------------------------------------------------------------------
# A1 — model identifiers are reviewed, not floating
# --------------------------------------------------------------------------


def test_configured_models_are_on_the_reviewed_allowlist() -> None:
    """`.env.example` and the allowlist must not drift apart."""
    for model in ("claude-sonnet-5", "claude-haiku-4-5-20251001"):
        assert model in REVIEWED_ANTHROPIC_MODELS


def test_production_rejects_an_unreviewed_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_BEHAVIOR_MODEL", raising=False)
    with pytest.raises(ValidationError, match="unreviewed Anthropic model"):
        RuntimeSettings(
            database_url="postgresql://u:p@h/db",  # type: ignore[arg-type]
            supabase_url="https://example.supabase.co",
            supabase_anon_key="anon",  # type: ignore[arg-type]
            supabase_service_role_key="service",  # type: ignore[arg-type]
            anthropic_api_key="key",  # type: ignore[arg-type]
            reranker_url="https://api.cohere.com/v2/rerank",
            reranker_api_key="rk",  # type: ignore[arg-type]
            hard_spend_cap_usd=Decimal("10"),
            anthropic_behavior_model="claude-sonnet-5-20260115",
        )


# --------------------------------------------------------------------------
# A4 / A6 — startup config guards
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:3000/",  # trailing slash
        "https://example.com/app",  # path
        "example.com",  # no scheme
        "https://example.com?x=1",  # query
    ],
)
def test_malformed_cors_origins_are_rejected(origin: str) -> None:
    with pytest.raises(ValidationError, match="CORS origin"):
        RuntimeSettings(
            database_url="postgresql://u:p@h/db",  # type: ignore[arg-type]
            supabase_url="https://example.supabase.co",
            supabase_anon_key="anon",  # type: ignore[arg-type]
            supabase_service_role_key="service",  # type: ignore[arg-type]
            anthropic_api_key="key",  # type: ignore[arg-type]
            reranker_url="https://api.cohere.com/v2/rerank",
            reranker_api_key="rk",  # type: ignore[arg-type]
            hard_spend_cap_usd=Decimal("10"),
            runtime_mode="development",  # type: ignore[arg-type]
            cors_allowed_origins=[origin],
        )


def test_production_refuses_a_placeholder_secret() -> None:
    with pytest.raises(ValidationError, match="still placeholder"):
        RuntimeSettings(
            database_url="postgresql://u:YOUR_DATABASE_PASSWORD@h/db",  # type: ignore[arg-type]
            supabase_url="https://example.supabase.co",
            supabase_anon_key="anon",  # type: ignore[arg-type]
            supabase_service_role_key="service",  # type: ignore[arg-type]
            anthropic_api_key="key",  # type: ignore[arg-type]
            voyage_api_key="voyage",  # type: ignore[arg-type]
            reranker_mode="hosted",  # type: ignore[arg-type]
            reranker_url="https://api.cohere.com/v2/rerank",
            reranker_api_key="rk",  # type: ignore[arg-type]
            reranker_model="rerank-v3.5",
            hard_spend_cap_usd=Decimal("10"),
            supabase_email_redirect_url="https://example.com/auth/confirmed",
            **_positive_pricing(),
        )


def test_production_refuses_the_development_reranker() -> None:
    # Every field is passed explicitly: `RuntimeSettings` also reads the
    # developer's real `.env`, so an under-specified case here would assert
    # against whatever happens to be on the machine.
    with pytest.raises(ValidationError, match="RERANKER_MODE=local"):
        RuntimeSettings(
            database_url="postgresql://u:realpassword@h/db",  # type: ignore[arg-type]
            supabase_url="https://example.supabase.co",
            supabase_anon_key="anonkey",  # type: ignore[arg-type]
            supabase_service_role_key="servicekey",  # type: ignore[arg-type]
            anthropic_api_key="antkey",  # type: ignore[arg-type]
            voyage_api_key="voyagekey",  # type: ignore[arg-type]
            reranker_mode="local",  # type: ignore[arg-type]
            reranker_url="http://127.0.0.1:8001/rerank",
            reranker_api_key="something",  # type: ignore[arg-type]
            reranker_model="cross-encoder/ms-marco-MiniLM-L-6-v2",
            hard_spend_cap_usd=Decimal("10"),
            runtime_mode="production",  # type: ignore[arg-type]
            anthropic_fast_model="claude-haiku-4-5-20251001",
            anthropic_behavior_model="claude-sonnet-5",
            anthropic_health_model="claude-sonnet-5",
            supabase_email_redirect_url="https://example.com/auth/confirmed",
            **_positive_pricing(),
        )


def _positive_pricing() -> dict[str, Decimal]:
    tiers = ("fast", "behavior", "health")
    kinds = ("input", "output", "cache_write", "cache_read")
    return {
        f"anthropic_{tier}_{kind}_cost_per_million_usd": Decimal("1")
        for tier in tiers
        for kind in kinds
    }


# --------------------------------------------------------------------------
# A5 — the auth contract can represent confirmation-pending
# --------------------------------------------------------------------------


def test_confirmation_pending_carries_no_session_material() -> None:
    response = AuthSessionResponse(status=AuthStatus.CONFIRMATION_REQUIRED)
    assert response.access_token is None
    assert response.refresh_token is None
    assert response.expires_in_seconds is None


def test_active_status_requires_complete_session_material() -> None:
    with pytest.raises(ValidationError, match="active auth session requires"):
        AuthSessionResponse(status=AuthStatus.ACTIVE, access_token="a")


def test_pending_status_rejects_partial_session_material() -> None:
    with pytest.raises(ValidationError, match="must not carry session material"):
        AuthSessionResponse(
            status=AuthStatus.CONFIRMATION_REQUIRED, access_token="leaked"
        )


# --------------------------------------------------------------------------
# B2 — credentials never survive into a log line
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "postgresql://postgres:hunter2@db.abc.supabase.co:5432/postgres",
        "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUv",
        "pa-abcdefghijklmnopqrstuvwxyz01",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N",
        'Authorization: "Bearer supersecretvalue"',
    ],
)
def test_credentials_are_redacted_from_log_text(secret: str) -> None:
    scrubbed = redact(f"connection failed: {secret}")
    for leaked in ("hunter2", "AbCdEfGhIjKlMnOpQrStUv", "supersecretvalue"):
        assert leaked not in scrubbed
    assert "eyJzdWIiOiIxMjM0NTY3ODkwIn0" not in scrubbed
