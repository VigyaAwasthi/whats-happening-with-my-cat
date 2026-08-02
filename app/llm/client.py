"""Anthropic structured-output client with caching, validation, and spend cap."""

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Generic, Protocol, TypeVar

import httpx
from pydantic import Field, PositiveInt, model_validator

from app.schemas.base import ContractModel
from app.db import Database
from app.schemas.enums import ToolErrorCode
from app.schemas.llm import (
    BehaviorCitation,
    BehaviorInterpretation,
    GroundednessVerdict,
    HealthSignalCheck,
    MemorySummary,
    SymptomIntake,
    TriageResult,
)
from app.schemas.enums import (
    AppetiteChange,
    BehaviorAnswerMode,
    BodySystem,
    ConfidenceLevel,
    TriageResponseKind,
    UrgencyTier,
    VomitingFrequency,
)
from app.tools.contracts import ToolError

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=ContractModel)


class ModelPurpose(str, Enum):
    """Model tier/purpose used for observability."""

    FAST = "fast"
    BEHAVIOR = "behavior"
    HEALTH = "health"


class StructuredCallResult(ContractModel, Generic[T]):
    """Exactly one validated value or typed failure."""

    value: T | None = Field(default=None, description="Validated structured output.")
    error: ToolError | None = Field(default=None, description="Typed call failure.")
    attempts: PositiveInt = Field(default=1, description="Requests attempted.")

    @model_validator(mode="after")
    def one_outcome(self) -> "StructuredCallResult[T]":
        if (self.value is None) == (self.error is None):
            raise ValueError("exactly one of value or error is required")
        return self


class StructuredLLMClient(Protocol):
    """No caller receives unvalidated model output."""

    async def generate(
        self,
        output_type: type[T],
        *,
        model: str,
        purpose: ModelPurpose,
        system_prompt: str,
        cache_context: str,
        user_prompt: str,
        max_tokens: int = 1024,
    ) -> StructuredCallResult[T]:
        """Return a Pydantic-validated object or typed fail-closed error."""
        ...


class AnthropicTransport(Protocol):
    """Mockable raw Messages API transport."""

    async def create_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return the decoded Anthropic response object."""
        ...

    async def count_input_tokens(self, payload: dict[str, Any]) -> int:
        """Return exact input tokens for the Messages payload before generation."""
        ...


class HttpAnthropicTransport:
    """Direct Messages API HTTP adapter."""

    def __init__(
        self, api_key: str, client: httpx.AsyncClient | None = None
    ) -> None:
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(
            base_url="https://api.anthropic.com", timeout=60
        )

    async def create_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(
            "/v1/messages",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    async def count_input_tokens(self, payload: dict[str, Any]) -> int:
        response = await self._client.post(
            "/v1/messages/count_tokens",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={key: value for key, value in payload.items() if key != "max_tokens"},
        )
        response.raise_for_status()
        input_tokens = int(response.json()["input_tokens"])
        if input_tokens < 0:
            raise ValueError("Anthropic returned a negative input-token count")
        return input_tokens


@dataclass(frozen=True)
class TokenPricing:
    """Per-model prices, including distinct prompt-cache billing categories."""

    input_per_million_usd: Decimal
    output_per_million_usd: Decimal
    cache_write_per_million_usd: Decimal
    cache_read_per_million_usd: Decimal


MONTHLY_WINDOW = "monthly"
LIFETIME_WINDOW = "lifetime"


def spend_budget_key(window: str, *, now: datetime | None = None) -> str:
    """Return the ledger row a spend window is accounted against.

    The window is encoded in the key itself, so a new period simply starts
    accumulating against a fresh row. Nothing has to run at the boundary: no
    cron, no migration, no operator action. A cap reached in one month stops
    blocking calls the instant the next month's key comes into use.

    ``monthly`` is the UTC calendar month — the boundary is 00:00 UTC on the
    first. ``lifetime`` reproduces the original cumulative behavior.
    """
    moment = now or datetime.now(timezone.utc)
    if window == MONTHLY_WINDOW:
        return f"global:{moment.astimezone(timezone.utc):%Y-%m}"
    return "global"


@dataclass(frozen=True)
class SpendReservation:
    """A conservative pre-call hold, bound to the window row that accepted it."""

    amount: Decimal
    budget_key: str


class SpendLedger(Protocol):
    """Atomic cumulative spend storage shared by every application worker."""

    async def try_reserve(
        self, budget_key: str, amount: Decimal, cap: Decimal
    ) -> Decimal | None:
        """Atomically add amount only when the cumulative cap permits it."""
        ...

    async def adjust(self, budget_key: str, delta: Decimal) -> Decimal:
        """Reconcile a conservative reservation and return the new total."""
        ...

    async def total(self, budget_key: str) -> Decimal:
        """Return the cumulative persisted total."""
        ...


class InMemorySpendLedger:
    """Process-local ledger reserved for tests; production uses PostgreSQL."""

    def __init__(self) -> None:
        self._totals: dict[str, Decimal] = {}
        self._lock = asyncio.Lock()

    async def try_reserve(
        self, budget_key: str, amount: Decimal, cap: Decimal
    ) -> Decimal | None:
        async with self._lock:
            current = self._totals.get(budget_key, Decimal("0"))
            if current + amount > cap:
                return None
            total = current + amount
            self._totals[budget_key] = total
            return total

    async def adjust(self, budget_key: str, delta: Decimal) -> Decimal:
        async with self._lock:
            total = max(
                Decimal("0"), self._totals.get(budget_key, Decimal("0")) + delta
            )
            self._totals[budget_key] = total
            return total

    async def total(self, budget_key: str) -> Decimal:
        async with self._lock:
            return self._totals.get(budget_key, Decimal("0"))


class PostgresSpendLedger:
    """Persistent atomic ledger; totals survive restarts and coordinate workers."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def try_reserve(
        self, budget_key: str, amount: Decimal, cap: Decimal
    ) -> Decimal | None:
        row = await self._database.fetch_one(
            """
            INSERT INTO llm_spend_totals (budget_key, spent_usd)
            SELECT %s, %s::numeric
            WHERE %s::numeric <= %s::numeric
            ON CONFLICT (budget_key) DO UPDATE
            SET spent_usd = llm_spend_totals.spent_usd + EXCLUDED.spent_usd,
                updated_at = now()
            WHERE llm_spend_totals.spent_usd + EXCLUDED.spent_usd <= %s::numeric
            RETURNING spent_usd
            """,
            (budget_key, amount, amount, cap, cap),
        )
        return None if row is None else Decimal(str(row["spent_usd"]))

    async def adjust(self, budget_key: str, delta: Decimal) -> Decimal:
        row = await self._database.fetch_one(
            """
            UPDATE llm_spend_totals
            SET spent_usd = GREATEST(0, spent_usd + %s),
                updated_at = now()
            WHERE budget_key = %s
            RETURNING spent_usd
            """,
            (delta, budget_key),
        )
        if row is None:
            raise RuntimeError("spend ledger reservation disappeared")
        return Decimal(str(row["spent_usd"]))

    async def total(self, budget_key: str) -> Decimal:
        row = await self._database.fetch_one(
            "SELECT spent_usd FROM llm_spend_totals WHERE budget_key = %s",
            (budget_key,),
        )
        return Decimal("0") if row is None else Decimal(str(row["spent_usd"]))


class SpendTracker:
    """Pre-call hard-cap accounting using per-model prices and a shared ledger."""

    def __init__(
        self,
        *,
        cap_usd: Decimal,
        pricing: Mapping[str, TokenPricing],
        ledger: SpendLedger | None = None,
        window: str = LIFETIME_WINDOW,
        warning_ratio: float = 0.8,
    ) -> None:
        self._cap = cap_usd
        self._pricing = dict(pricing)
        self._ledger = ledger or InMemorySpendLedger()
        self._window = window
        self._warning_threshold = cap_usd * Decimal(str(warning_ratio))
        self._last_known_total = Decimal("0")
        self._warned_for_key: str | None = None

    @property
    def budget_key(self) -> str:
        """The ledger row the current instant accounts against."""
        return spend_budget_key(self._window)

    async def reserve(
        self, model: str, exact_input: int, max_output: int
    ) -> SpendReservation | None:
        pricing = self._model_pricing(model)
        conservative_input_rate = max(
            pricing.input_per_million_usd,
            pricing.cache_write_per_million_usd,
            pricing.cache_read_per_million_usd,
        )
        amount = self._cost(
            exact_input,
            max_output,
            input_rate=conservative_input_rate,
            output_rate=pricing.output_per_million_usd,
        )
        # Resolve the key once and carry it on the reservation, so a window
        # boundary crossed mid-call reconciles against the row that was
        # actually debited rather than the new period's empty row.
        budget_key = self.budget_key
        total = await self._ledger.try_reserve(budget_key, amount, self._cap)
        if total is None:
            logger.error(
                "llm_spend_cap_reached budget_key=%s cap_usd=%s window=%s",
                budget_key,
                self._cap,
                self._window,
            )
            return None
        self._last_known_total = total
        self._warn_if_approaching_cap(budget_key, total)
        return SpendReservation(amount=amount, budget_key=budget_key)

    async def reconcile(
        self,
        model: str,
        reservation: SpendReservation,
        *,
        actual_input: int,
        actual_output: int,
        cache_write_input: int,
        cache_read_input: int,
    ) -> None:
        pricing = self._model_pricing(model)
        actual = (
            self._cost(
                actual_input,
                actual_output,
                input_rate=pricing.input_per_million_usd,
                output_rate=pricing.output_per_million_usd,
            )
            + self._cost(
                cache_write_input,
                0,
                input_rate=pricing.cache_write_per_million_usd,
                output_rate=Decimal("0"),
            )
            + self._cost(
                cache_read_input,
                0,
                input_rate=pricing.cache_read_per_million_usd,
                output_rate=Decimal("0"),
            )
        )
        self._last_known_total = await self._ledger.adjust(
            reservation.budget_key, actual - reservation.amount
        )
        self._warn_if_approaching_cap(reservation.budget_key, self._last_known_total)

    def _warn_if_approaching_cap(self, budget_key: str, total: Decimal) -> None:
        """Warn once per worker per window, well before calls start failing."""
        if total < self._warning_threshold or self._warned_for_key == budget_key:
            return
        self._warned_for_key = budget_key
        logger.warning(
            "llm_spend_approaching_cap budget_key=%s spent_usd=%s cap_usd=%s "
            "used_ratio=%.3f window=%s",
            budget_key,
            total,
            self._cap,
            float(total / self._cap) if self._cap else 0.0,
            self._window,
        )

    @property
    def spent_usd(self) -> Decimal:
        """Last total observed by this worker; use current_spend for authoritative state."""
        return self._last_known_total

    async def current_spend(self) -> Decimal:
        """Read the authoritative value for the current window from the ledger."""
        return await self._ledger.total(self.budget_key)

    def pricing_for(self, model: str) -> TokenPricing | None:
        """Rates for a model, or None. Used by tracing, which must not raise."""
        return self._pricing.get(model)

    def _model_pricing(self, model: str) -> TokenPricing:
        try:
            return self._pricing[model]
        except KeyError as exc:
            raise ValueError(f"no token pricing configured for model {model!r}") from exc

    @staticmethod
    def _cost(
        input_tokens: int,
        output_tokens: int,
        *,
        input_rate: Decimal,
        output_rate: Decimal,
    ) -> Decimal:
        million = Decimal(1_000_000)
        return (
            Decimal(input_tokens) * input_rate
            + Decimal(output_tokens) * output_rate
        ) / million


class AnthropicStructuredClient:
    """Schema-constrained Anthropic client; validation retries once then closes."""

    def __init__(
        self, transport: AnthropicTransport, spend_tracker: SpendTracker
    ) -> None:
        self._transport = transport
        self._spend = spend_tracker

    async def generate(
        self,
        output_type: type[T],
        *,
        model: str,
        purpose: ModelPurpose,
        system_prompt: str,
        cache_context: str,
        user_prompt: str,
        max_tokens: int = 1024,
    ) -> StructuredCallResult[T]:
        payload = _payload(
            output_type,
            model=model,
            system_prompt=system_prompt,
            cache_context=cache_context,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
        )
        try:
            exact_input = await self._transport.count_input_tokens(payload)
            reservation = await self._spend.reserve(
                model, exact_input * 2, max_tokens * 2
            )
        except Exception as exc:
            logger.exception("LLM spend-cap preflight failed closed")
            return StructuredCallResult(
                error=ToolError(
                    code=ToolErrorCode.UNAVAILABLE,
                    message=str(exc) or "spend-cap preflight unavailable",
                    retryable=False,
                )
            )
        if reservation is None:
            return StructuredCallResult(
                error=ToolError(
                    code=ToolErrorCode.UNAVAILABLE,
                    message="hard LLM spend cap reached",
                    retryable=False,
                )
            )

        last_error = "structured output validation failed"
        actual_input = 0
        actual_output = 0
        cache_write_input = 0
        cache_read_input = 0
        for attempt in range(1, 3):
            started = time.perf_counter()
            validation_outcome = "failed"
            response: dict[str, Any] = {}
            try:
                response = await self._transport.create_message(payload)
                usage = response.get("usage", {})
                actual_input += int(usage.get("input_tokens", 0))
                actual_output += int(usage.get("output_tokens", 0))
                cache_write_input += int(
                    usage.get("cache_creation_input_tokens", 0)
                )
                cache_read_input += int(usage.get("cache_read_input_tokens", 0))
                if response.get("stop_reason") in {"refusal", "max_tokens"}:
                    raise ValueError(
                        f"unsafe stop reason: {response.get('stop_reason')}"
                    )
                text_blocks = [
                    block["text"]
                    for block in response.get("content", [])
                    if block.get("type") == "text"
                ]
                if len(text_blocks) != 1:
                    raise ValueError("expected exactly one structured text block")
                value = output_type.model_validate_json(text_blocks[0])
                validation_outcome = "passed"
                await _reconcile_spend_safely(
                    self._spend,
                    model,
                    reservation,
                    actual_input,
                    actual_output,
                    cache_write_input,
                    cache_read_input,
                )
                _log_call(
                    model,
                    purpose,
                    started,
                    usage,
                    validation_outcome,
                    attempt,
                    self._spend.pricing_for(model),
                )
                return StructuredCallResult(value=value, attempts=attempt)
            except (
                httpx.HTTPError,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                last_error = str(exc)
                _log_call(
                    model,
                    purpose,
                    started,
                    response.get("usage", {}),
                    validation_outcome,
                    attempt,
                    self._spend.pricing_for(model),
                )

        await _reconcile_spend_safely(
            self._spend,
            model,
            reservation,
            actual_input,
            actual_output,
            cache_write_input,
            cache_read_input,
        )
        return StructuredCallResult(
            error=ToolError(
                code=ToolErrorCode.INVALID_INPUT,
                message=last_error or "model output validation failed",
                retryable=False,
            ),
            attempts=2,
        )


class DevelopmentStructuredClient:
    """Deterministic, zero-paid-call client for local API exercises."""

    def __init__(self) -> None:
        self.calls: list[tuple[ModelPurpose, type[ContractModel]]] = []

    async def generate(
        self,
        output_type: type[T],
        *,
        model: str,
        purpose: ModelPurpose,
        system_prompt: str,
        cache_context: str,
        user_prompt: str,
        max_tokens: int = 1024,
    ) -> StructuredCallResult[T]:
        self.calls.append((purpose, output_type))
        started = time.perf_counter()
        await asyncio.sleep(0)
        value = _development_value(output_type, user_prompt, cache_context)
        # Recorded so trace shape, model-call counts, and the "no model call"
        # safety assertions are exercised by the zero-cost development path too.
        _record_call_on_trace(
            model,
            purpose,
            (time.perf_counter() - started) * 1000,
            {},
            "passed" if value is not None else "failed",
            1,
            None,
            "development",
        )
        if value is None:
            return StructuredCallResult(
                error=ToolError(
                    code=ToolErrorCode.UNAVAILABLE,
                    message="development client has no output for schema",
                    retryable=False,
                )
            )
        return StructuredCallResult(value=value)


def _payload(
    output_type: type[ContractModel],
    *,
    model: str,
    system_prompt: str,
    cache_context: str,
    user_prompt: str,
    max_tokens: int,
) -> dict[str, Any]:
    system_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    if cache_context:
        system_blocks.append(
            {
                "type": "text",
                "text": f"Retrieved context:\n{cache_context}",
            }
        )
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_blocks,
        "messages": [{"role": "user", "content": user_prompt}],
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": output_type.model_json_schema(),
            }
        },
    }


async def _reconcile_spend_safely(
    tracker: SpendTracker,
    model: str,
    reservation: SpendReservation,
    actual_input: int,
    actual_output: int,
    cache_write_input: int,
    cache_read_input: int,
) -> None:
    """Keep a conservative reservation when reconciliation storage is unavailable."""
    try:
        await tracker.reconcile(
            model,
            reservation,
            actual_input=actual_input,
            actual_output=actual_output,
            cache_write_input=cache_write_input,
            cache_read_input=cache_read_input,
        )
    except Exception:
        logger.exception("LLM spend reconciliation failed; reservation retained")


def _log_call(
    model: str,
    purpose: ModelPurpose,
    started: float,
    usage: dict[str, Any],
    validation: str,
    attempt: int,
    pricing: TokenPricing | None = None,
    prompt_version: str = "v1",
) -> None:
    latency_ms = (time.perf_counter() - started) * 1000
    _record_call_on_trace(
        model, purpose, latency_ms, usage, validation, attempt, pricing, prompt_version
    )
    logger.info(
        "llm_call model=%s purpose=%s latency_ms=%.1f input_tokens=%s "
        "output_tokens=%s cache_read=%s cache_creation=%s validation=%s attempt=%s",
        model,
        purpose.value,
        (time.perf_counter() - started) * 1000,
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        usage.get("cache_read_input_tokens", 0),
        usage.get("cache_creation_input_tokens", 0),
        validation,
        attempt,
    )


def _record_call_on_trace(
    model: str,
    purpose: ModelPurpose,
    latency_ms: float,
    usage: dict[str, Any],
    validation: str,
    attempt: int,
    pricing: TokenPricing | None,
    prompt_version: str,
) -> None:
    """Attach this call to the in-flight generation trace, if there is one.

    Tracing is observational: a fault here must never disturb the answer, so
    every failure is swallowed. Imported lazily to keep the model client free of
    an import-time dependency on the observability package.
    """
    try:
        from app.observability.collector import current_trace
        from app.schemas.trace import ModelCallTrace

        collector = current_trace()
        if collector is None:
            return
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
        cache_write = int(usage.get("cache_creation_input_tokens", 0) or 0)
        cost = Decimal("0")
        if pricing is not None:
            million = Decimal(1_000_000)
            cost = (
                Decimal(input_tokens) * pricing.input_per_million_usd
                + Decimal(output_tokens) * pricing.output_per_million_usd
                + Decimal(cache_read) * pricing.cache_read_per_million_usd
                + Decimal(cache_write) * pricing.cache_write_per_million_usd
            ) / million
        collector.record_model_call(
            ModelCallTrace(
                purpose=purpose.value,
                model=model,
                prompt_version=prompt_version,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                latency_ms=max(0.0, latency_ms),
                validation=validation,
                attempts=attempt,
                cost_usd=float(cost),
            )
        )
    except Exception:  # pragma: no cover - observability must not break requests
        logger.debug("generation trace: model call not recorded", exc_info=True)


def _development_value(
    output_type: type[T], user_prompt: str, context: str
) -> T | None:
    text = user_prompt.casefold()
    if output_type is HealthSignalCheck:
        medical_terms = ("vomit", "blood", "not eating", "breath", "pee", "seizure")
        matched = [term for term in medical_terms if term in text]
        return output_type.model_validate(
            {
                "has_medical_signal": bool(matched),
                "confidence": 0.95 if matched else 0.1,
                "matched_terms": matched,
            }
        )
    if output_type is SymptomIntake:
        return output_type.model_validate(
            {
                "body_systems": (
                    [BodySystem.DIGESTIVE.value]
                    if "vomit" in text
                    else []
                ),
                "duration_hours": None,
                "appetite_change": AppetiteChange.UNKNOWN.value,
                "vomiting": (
                    VomitingFrequency.REPEATED.value
                    if "vomit" in text
                    else VomitingFrequency.UNKNOWN.value
                ),
                "litter_box_change": None,
                "breathing_change": True if "breath" in text else None,
                "lethargy": True if "letharg" in text else None,
                "free_text_residual": user_prompt,
            }
        )
    if output_type is MemorySummary:
        return output_type.model_validate(
            {
                "summary": user_prompt[:500] or "No durable facts.",
                "salient_facts": [],
                "covers_message_count": max(1, user_prompt.count("\n") + 1),
            }
        )
    if output_type is GroundednessVerdict:
        return output_type.model_validate(
            {"passed": True, "unsupported_claims": [], "notes": "development"}
        )
    if output_type is BehaviorInterpretation:
        ids = _context_ids(context)
        if ids:
            source = _context_behavior_source(context, ids[0])
            return output_type.model_validate(
                {
                    "interpretation": (
                        "This may be normal play, curiosity, or attention-seeking "
                        "behavior in the context described."
                    ),
                    "answer_mode": BehaviorAnswerMode.CORPUS_GROUNDED.value,
                    "confidence": ConfidenceLevel.GENERAL.value,
                    "reasoning": (
                        "The interpretation is limited to the retrieved behavior "
                        "context."
                    ),
                    "cited_entry_ids": [ids[0]],
                    "retrieved_entry_ids": ids,
                    "cited_entries": [
                        BehaviorCitation(
                            entry_id=ids[0],
                            title=source[0],
                            organization=source[1],
                            url=source[2],
                        ).model_dump(mode="json")
                    ],
                    "suggested_clarifying_questions": [],
                    "medical_nudge": False,
                }
            )
        profile = _context_profile_facts(context)
        return output_type.model_validate(
            {
                "interpretation": (
                    "That sounds like a curious cat-specific routine. The location, "
                    "timing, and what happens next can help distinguish play, comfort, "
                    "attention-seeking, and simple sensory interest."
                ),
                "answer_mode": BehaviorAnswerMode.GENERAL_KNOWLEDGE.value,
                "confidence": ConfidenceLevel.VARIES_BY_CAT.value,
                "reasoning": (
                    "This is general feline understanding personalized with: "
                    + (", ".join(profile) if profile else "the active cat profile")
                ),
                "cited_entry_ids": [],
                "retrieved_entry_ids": [],
                "cited_entries": [],
                "suggested_clarifying_questions": [
                    "What usually happens immediately before this?"
                ],
                "medical_nudge": False,
            }
        )
    if output_type is TriageResult:
        ids = _context_ids(context)
        if not ids:
            return None
        return output_type.model_validate(
            {
                "severity": UrgencyTier.ROUTINE.value,
                "claims": [
                    {
                        "text": "The retrieved source recommends veterinary assessment.",
                        "source_entry_id": ids[0],
                        "source_url": None,
                    }
                ],
                "message": "The retrieved information supports veterinary assessment.",
                "retrieved_entry_ids": ids,
                "response_kind": TriageResponseKind.TRIAGE.value,
            }
        )
    return None


def _context_ids(context: str) -> list[str]:
    """Development-only context convention; production output is never parsed this way."""
    return [
        line.removeprefix("ENTRY_ID: ").strip()
        for line in context.splitlines()
        if line.startswith("ENTRY_ID: ")
    ]


def _context_behavior_source(
    context: str, entry_id: str
) -> tuple[str, str, str | None]:
    """Read the development context convention used only by the local stub."""
    marker = f"ENTRY_ID: {entry_id}"
    block = context.partition(marker)[2].split("\n\n", 1)[0]
    source_line = next(
        (line.removeprefix("SOURCES: ") for line in block.splitlines() if line.startswith("SOURCES: ")),
        "",
    )
    first = source_line.split(" | ", 1)[0]
    parts = [part.strip() for part in first.split(" :: ")]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2] or None
    return "Curated behavior source", "Curated corpus", None


def _context_profile_facts(context: str) -> list[str]:
    """Extract local-stub personalization inputs from the explicit profile block."""
    marker = "ACTIVE CAT PROFILE:\n"
    if marker not in context:
        return []
    block = context.partition(marker)[2].split("\n\n", 1)[0]
    return [line.strip() for line in block.splitlines() if line.strip()]
