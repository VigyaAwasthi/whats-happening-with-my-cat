"""Regression tests for confirmed-email signup and account provisioning."""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import UUID, uuid4

import httpx
from pydantic import SecretStr

from app.repositories.application import SupabaseAuthService
from app.schemas.api import AuthSessionRequest
from app.schemas.enums import AuthStatus


class FakeDatabase:
    def __init__(self) -> None:
        self.account_id = uuid4()
        self.provisioned_subjects: list[str] = []

    async def fetch_all(
        self,
        query: str,
        params: Any = None,
    ) -> list[dict[str, Any]]:
        return []

    async def fetch_one(
        self,
        query: str,
        params: Any = None,
    ) -> dict[str, Any] | None:
        if "INSERT INTO accounts" in query:
            assert params is not None
            self.provisioned_subjects.append(str(params[0]))
            return {"id": self.account_id}

        if "SELECT id FROM accounts" in query:
            return None

        return None

    async def execute(self, query: str, params: Any = None) -> int:
        if "INSERT INTO accounts" in query:
            assert params is not None
            self.provisioned_subjects.append(str(params[0]))
            return 1
        return 0

    def transaction(self) -> AbstractAsyncContextManager["FakeDatabase"]:
        raise NotImplementedError


def auth_request() -> AuthSessionRequest:
    return AuthSessionRequest(
        email="new-user@example.com",
        password=SecretStr("A-secure-test-password-123!"),
    )


async def test_signup_accepts_top_level_identity_and_sends_redirect_query() -> None:
    subject = uuid4()
    database = FakeDatabase()
    observed_redirect: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_redirect
        observed_redirect = request.url.params.get("redirect_to")

        return httpx.Response(
            200,
            json={
                "id": str(subject),
                "email": "new-user@example.com",
            },
        )

    async with httpx.AsyncClient(
        base_url="https://project.supabase.co",
        transport=httpx.MockTransport(handler),
    ) as client:
        service = SupabaseAuthService(
            supabase_url="https://project.supabase.co",
            anon_key="anon-test-key",
            service_role_key="service-role-test-key",
            database=database,  # type: ignore[arg-type]
            email_redirect_url="https://app.example.com/auth/confirmed",
            client=client,
        )

        result = await service.sign_up(auth_request())

    assert result.status is AuthStatus.CONFIRMATION_REQUIRED
    assert observed_redirect == "https://app.example.com/auth/confirmed"
    assert database.provisioned_subjects == [str(subject)]


async def test_signup_without_identity_returns_confirmation_required() -> None:
    database = FakeDatabase()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(
        base_url="https://project.supabase.co",
        transport=httpx.MockTransport(handler),
    ) as client:
        service = SupabaseAuthService(
            supabase_url="https://project.supabase.co",
            anon_key="anon-test-key",
            service_role_key="service-role-test-key",
            database=database,  # type: ignore[arg-type]
            email_redirect_url="https://app.example.com/auth/confirmed",
            client=client,
        )

        result = await service.sign_up(auth_request())

    assert result.status is AuthStatus.CONFIRMATION_REQUIRED
    assert database.provisioned_subjects == []


async def test_resolve_account_provisions_missing_internal_account() -> None:
    subject = uuid4()
    database = FakeDatabase()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/v1/user"
        return httpx.Response(200, json={"id": str(subject)})

    async with httpx.AsyncClient(
        base_url="https://project.supabase.co",
        transport=httpx.MockTransport(handler),
    ) as client:
        service = SupabaseAuthService(
            supabase_url="https://project.supabase.co",
            anon_key="anon-test-key",
            service_role_key="service-role-test-key",
            database=database,  # type: ignore[arg-type]
            client=client,
        )

        resolved = await service.resolve_account("confirmed-access-token")

    assert resolved == database.account_id
    assert database.provisioned_subjects == [str(subject)]
