"""Regression tests for the pre-launch audit fixes."""

import asyncio
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.llm.client import (
    AnthropicStructuredClient,
    ModelPurpose,
    SpendTracker,
    TokenPricing,
    _payload,
)
from app.ingestion.embeddings import VoyageEmbeddingProvider, _rate_limit_delay
from app.orchestration.health import (
    _strip_unsupported,
    _stripping_establishes_groundedness,
)
from app.schemas.enums import TriageResponseKind, UrgencyTier
from app.schemas.llm import Claim, SymptomIntake, TriageResult


class CountingTransport:
    def __init__(self) -> None:
        self.message_calls = 0

    async def count_input_tokens(self, payload: dict[str, Any]) -> int:
        return 1_000

    async def create_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.message_calls += 1
        raise AssertionError("spend cap must refuse before a Messages API call")


def _pricing(input_rate: str = "1", output_rate: str = "1") -> TokenPricing:
    return TokenPricing(
        input_per_million_usd=Decimal(input_rate),
        output_per_million_usd=Decimal(output_rate),
        cache_write_per_million_usd=Decimal(input_rate),
        cache_read_per_million_usd=Decimal(input_rate),
    )


def test_triage_response_kind_controls_claim_requirement() -> None:
    with pytest.raises(ValidationError, match="response_kind is triage"):
        TriageResult(
            severity=UrgencyTier.ROUTINE,
            claims=[],
            message="No claims.",
            retrieved_entry_ids=[],
            response_kind=TriageResponseKind.TRIAGE,
        )

    for kind in (
        TriageResponseKind.EMERGENCY_CANNED,
        TriageResponseKind.NO_RELIABLE_INFORMATION,
    ):
        result = TriageResult(
            severity=UrgencyTier.EMERGENCY,
            claims=[],
            message="Code-controlled response.",
            retrieved_entry_ids=[],
            response_kind=kind,
        )
        assert result.response_kind is kind


def test_prompt_cache_checkpoint_precedes_dynamic_context() -> None:
    payload = _payload(
        SymptomIntake,
        model="fast",
        system_prompt="byte-stable system rules",
        cache_context="cat-specific profile and retrieved entries",
        user_prompt="current user message",
        max_tokens=100,
    )
    assert payload["system"][0] == {
        "type": "text",
        "text": "byte-stable system rules",
        "cache_control": {"type": "ephemeral"},
    }
    assert payload["system"][1]["text"].startswith("Retrieved context:")
    assert "cache_control" not in payload["system"][1]


async def test_spend_cap_refuses_before_messages_api_call() -> None:
    transport = CountingTransport()
    client = AnthropicStructuredClient(
        transport,
        SpendTracker(
            cap_usd=Decimal("0.000001"),
            pricing={"fast": _pricing()},
        ),
    )
    result = await client.generate(
        SymptomIntake,
        model="fast",
        purpose=ModelPurpose.FAST,
        system_prompt="rules",
        cache_context="",
        user_prompt="symptoms",
        max_tokens=100,
    )
    assert result.error is not None
    assert result.error.message == "hard LLM spend cap reached"
    assert transport.message_calls == 0


def test_stripped_health_draft_requires_a_deterministic_final_pass() -> None:
    draft = TriageResult(
        severity=UrgencyTier.URGENT,
        claims=[
            Claim(text="Supported.", source_entry_id="entry-1"),
            Claim(text="Unsupported.", source_entry_id="entry-1"),
        ],
        message="Supported. Unsupported.",
        retrieved_entry_ids=["entry-1"],
        response_kind=TriageResponseKind.TRIAGE,
    )
    stripped = _strip_unsupported(draft, ["Unsupported."])
    assert stripped is not None
    assert _stripping_establishes_groundedness(
        draft, stripped, ["Unsupported."], {"entry-1"}
    )
    assert _strip_unsupported(draft, ["A paraphrase the code cannot map"]) is None


class _EmbeddingHTTPClient:
    def __init__(self) -> None:
        self.calls = 0

    async def post(self, path: str, **kwargs: object) -> httpx.Response:
        self.calls += 1
        await asyncio.sleep(0)
        return httpx.Response(
            200,
            request=httpx.Request("POST", f"https://voyage.test{path}"),
            json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]},
        )


async def test_identical_concurrent_queries_share_one_embedding_call() -> None:
    http = _EmbeddingHTTPClient()
    provider = VoyageEmbeddingProvider(
        api_key="test",
        model="test-model",
        dimensions=3,
        client=http,
    )
    first, second = await asyncio.gather(
        provider.embed_query("same query"),
        provider.embed_query("same query"),
    )
    assert first == second == [0.1, 0.2, 0.3]
    assert http.calls == 1


def test_voyage_429_without_header_uses_reduced_tier_pacing() -> None:
    response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://voyage.test/v1/embeddings"),
    )
    assert _rate_limit_delay(response) >= 20
