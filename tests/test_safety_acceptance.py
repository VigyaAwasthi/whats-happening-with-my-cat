"""The ten safety acceptance tests required by Phase 2."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.corpus_paths import resolve_corpus_dir
from app.ingestion.csv_loader import load_behavior, load_health
from app.ingestion.embeddings import DeterministicEmbeddingProvider
from app.llm.client import (
    AnthropicStructuredClient,
    ModelPurpose,
    SpendTracker,
    StructuredCallResult,
    TokenPricing,
)
from app.memory.repository import InMemoryMemoryRepository
from app.memory.service import CatMemoryService, PostgresMemoryRetriever
from app.orchestration.behavior import BehaviorOrchestrator
from app.orchestration.health import HealthOrchestrator
from app.safety.groundedness import CompositeGroundednessValidator
from app.safety.red_flags import DeterministicRedFlagChecker
from app.schemas.api import BehaviorChatRequest, HealthChatRequest
from app.schemas.enums import (
    AppetiteChange,
    BodySystem,
    ConfidenceLevel,
    Corner,
    MessageRole,
    TriageResponseKind,
    ToolErrorCode,
    UrgencyTier,
    VomitingFrequency,
)
from app.schemas.llm import (
    BehaviorInterpretation,
    GroundednessVerdict,
    HealthSignalCheck,
    MemorySummary,
    SymptomIntake,
    TriageResult,
)
from app.schemas.memory import MemoryResult
from app.tools.contracts import (
    BehaviorKnowledgeRetrieverOutput,
    MemoryRetrieverOutput,
    RankedBehaviorEntry,
    RankedHealthEntry,
    RetrievalScores,
    ToolError,
    VetKnowledgeRetrieverOutput,
)


CORPUS_DIR = resolve_corpus_dir()
HEALTH_ENTRY = load_health(CORPUS_DIR / "MASTER_health_corpus.csv")[0]
BEHAVIOR_ENTRY = load_behavior(CORPUS_DIR / "MASTER_behavior_corpus.csv")[0]


class FakeLLM:
    """Schema-keyed structured client with observable purpose calls."""

    def __init__(self, values: dict[type[object], object] | None = None) -> None:
        self.values = values or {}
        self.calls: list[tuple[type[object], ModelPurpose]] = []

    async def generate(
        self,
        output_type: type[object],
        *,
        model: str,
        purpose: ModelPurpose,
        system_prompt: str,
        cache_context: str,
        user_prompt: str,
        max_tokens: int = 1024,
    ) -> StructuredCallResult:
        self.calls.append((output_type, purpose))
        value = self.values.get(output_type, _default_value(output_type))
        if value is None:
            return StructuredCallResult(
                error=ToolError(
                    code=ToolErrorCode.INVALID_INPUT,
                    message="mock structured failure",
                    retryable=False,
                )
            )
        return StructuredCallResult(value=value)


class StaticVetRetriever:
    def __init__(self, include_entry: bool) -> None:
        self.include_entry = include_entry

    async def retrieve(self, request: object) -> VetKnowledgeRetrieverOutput:
        if not self.include_entry:
            return VetKnowledgeRetrieverOutput(entries=[])
        return VetKnowledgeRetrieverOutput(
            entries=[
                RankedHealthEntry(
                    entry_id=HEALTH_ENTRY.id,
                    scores=RetrievalScores(
                        lexical=1.0, semantic=1.0, rerank=1.0
                    ),
                    entry=HEALTH_ENTRY,
                )
            ]
        )


class StaticBehaviorRetriever:
    async def retrieve(self, request: object) -> BehaviorKnowledgeRetrieverOutput:
        return BehaviorKnowledgeRetrieverOutput(
            entries=[
                RankedBehaviorEntry(
                    entry_id=BEHAVIOR_ENTRY.id,
                    scores=RetrievalScores(
                        lexical=1.0, semantic=1.0, rerank=1.0
                    ),
                    entry=BEHAVIOR_ENTRY,
                )
            ]
        )


class EmptyMemoryRetriever:
    async def retrieve(self, request: object) -> MemoryRetrieverOutput:
        return MemoryRetrieverOutput(
            result=MemoryResult(
                cat_id=request.cat_id,
                profile_facts=[],
                relevant_summaries=[],
            )
        )


class RecordingMemoryWriter:
    def __init__(self) -> None:
        self.exchanges: list[dict[str, object]] = []

    async def record_exchange(self, **kwargs: object) -> UUID:
        self.exchanges.append(kwargs)
        return kwargs["requested_session_id"]

    async def working_context(self, *args: object) -> list[str]:
        return []


def health_orchestrator(
    llm: object, *, include_entry: bool
) -> HealthOrchestrator:
    memory = RecordingMemoryWriter()
    groundedness = CompositeGroundednessValidator(llm, "fast")
    return HealthOrchestrator(
        llm=llm,
        fast_model="fast",
        health_model="health-strong",
        vet_retriever=StaticVetRetriever(include_entry),
        memory_retriever=EmptyMemoryRetriever(),
        memory_writer=memory,
        red_flags=DeterministicRedFlagChecker(),
        groundedness=groundedness,
    )


@pytest.mark.parametrize(
    ("message", "expected_rule", "severity"),
    [
        (
            "Straining in the litter box and nothing comes out",
            "urinary_obstruction",
            UrgencyTier.EMERGENCY,
        ),
        (
            "She is breathing with her mouth open",
            "breathing_difficulty",
            UrgencyTier.EMERGENCY,
        ),
        ("She had a seizure", "seizure", UrgencyTier.EMERGENCY),
        ("He collapsed", "collapse", UrgencyTier.EMERGENCY),
        (
            "She got into antifreeze",
            "toxin_ingestion",
            UrgencyTier.EMERGENCY,
        ),
        (
            "He swallowed my pill",
            "human_medication",
            UrgencyTier.EMERGENCY,
        ),
        ("She licked a lily", "lily_exposure", UrgencyTier.EMERGENCY),
        ("His gums are blue", "abnormal_gums", UrgencyTier.EMERGENCY),
        (
            "She hasn't eaten for 3 days",
            "not_eating_48h",
            UrgencyTier.URGENT,
        ),
        (
            "There is blood in her vomit",
            "vomiting_blood",
            UrgencyTier.URGENT,
        ),
        (
            "She is lethargic and keeps vomiting",
            "vomiting_with_lethargy",
            UrgencyTier.URGENT,
        ),
        (
            "He cannot put weight on the leg",
            "cannot_bear_weight",
            UrgencyTier.URGENT,
        ),
        ("Her eye is cloudy", "painful_eye", UrgencyTier.URGENT),
    ],
)
def test_every_coded_raw_red_flag(
    message: str, expected_rule: str, severity: UrgencyTier
) -> None:
    result = DeterministicRedFlagChecker().check_raw(message)
    assert expected_rule in result.matched_rules
    assert result.severity is severity


@pytest.mark.parametrize(
    "message",
    [
        "My cat can't pee and keeps going to the litter box",
        "He ate a lily",
    ],
)
async def test_emergencies_bypass_strong_generation(message: str) -> None:
    llm = FakeLLM()
    orchestrator = health_orchestrator(llm, include_entry=True)
    response = await orchestrator.handle(
        HealthChatRequest(
            cat_id=uuid4(),
            message=message,
            intake=None,
            session_id=uuid4(),
        )
    )
    assert response.result.severity is UrgencyTier.EMERGENCY
    assert response.result.response_kind is TriageResponseKind.EMERGENCY_CANNED
    assert not any(purpose is ModelPurpose.HEALTH for _, purpose in llm.calls)
    assert llm.calls == []


async def test_keyword_screen_fires_when_extractor_would_miss() -> None:
    empty_intake = SymptomIntake(
        body_systems=[],
        duration_hours=None,
        appetite_change=AppetiteChange.UNKNOWN,
        vomiting=VomitingFrequency.UNKNOWN,
        litter_box_change=None,
        breathing_change=None,
        lethargy=None,
        free_text_residual="",
    )
    llm = FakeLLM({SymptomIntake: empty_intake})
    response = await health_orchestrator(llm, include_entry=True).handle(
        HealthChatRequest(
            cat_id=uuid4(),
            message="She swallowed a pill",
            intake=None,
            session_id=uuid4(),
        )
    )
    assert response.result.severity is UrgencyTier.EMERGENCY
    assert llm.calls == []


async def test_no_relevant_health_match_refuses_instead_of_inventing() -> None:
    llm = FakeLLM()
    response = await health_orchestrator(llm, include_entry=False).handle(
        HealthChatRequest(
            cat_id=uuid4(),
            message="A symptom absent from the curated corpus",
            intake=None,
            session_id=uuid4(),
        )
    )
    assert response.result.response_kind is TriageResponseKind.NO_RELIABLE_INFORMATION
    assert response.result.claims == []
    assert not any(purpose is ModelPurpose.HEALTH for _, purpose in llm.calls)


async def test_invalid_citation_model_json_never_reaches_user() -> None:
    transport = SchemaTransport(invalid_triage_citation=True)
    client = AnthropicStructuredClient(
        transport,
        SpendTracker(
            cap_usd=Decimal("100"),
            pricing=_zero_pricing("fast", "health-strong"),
        ),
    )
    response = await health_orchestrator(client, include_entry=True).handle(
        HealthChatRequest(
            cat_id=uuid4(),
            message="She seems mildly unwell",
            intake=None,
            session_id=uuid4(),
        )
    )
    assert response.result.response_kind is TriageResponseKind.NO_RELIABLE_INFORMATION
    assert "fabricated medical claim" not in response.result.message


async def test_cross_cat_memory_read_returns_zero_leakage() -> None:
    repository = InMemoryMemoryRepository()
    cat_a, cat_b, session = uuid4(), uuid4(), uuid4()
    await repository.ensure_session(cat_a, session, Corner.BEHAVIOR)
    await repository.write_long_term(
        cat_a, session, "Cat A loves a red wand toy.", None
    )
    retriever = PostgresMemoryRetriever(
        repository, DeterministicEmbeddingProvider()
    )
    result = await retriever.retrieve(
        type(
            "Request",
            (),
            {"cat_id": cat_b, "query": "wand toy", "limit": 5},
        )()
    )
    assert result.result is not None
    assert result.result.cat_id == cat_b
    assert result.result.relevant_summaries == []


async def test_behavior_vomiting_blood_nudges_without_interpretation() -> None:
    llm = FakeLLM()
    memory = RecordingMemoryWriter()
    orchestrator = BehaviorOrchestrator(
        llm=llm,
        fast_model="fast",
        behavior_model="behavior-strong",
        health_signal_threshold=0.7,
        behavior_retriever=StaticBehaviorRetriever(),
        memory_retriever=EmptyMemoryRetriever(),
        memory_writer=memory,
        red_flags=DeterministicRedFlagChecker(),
        groundedness=CompositeGroundednessValidator(llm, "fast"),
    )
    response = await orchestrator.handle(
        BehaviorChatRequest(
            cat_id=uuid4(),
            message="She's been vomiting blood",
            session_id=uuid4(),
        )
    )
    assert response.result.medical_nudge is True
    assert not any(purpose is ModelPurpose.BEHAVIOR for _, purpose in llm.calls)


async def test_cat_switch_creates_new_session_without_context() -> None:
    repository = InMemoryMemoryRepository()
    cat_a, cat_b, requested = uuid4(), uuid4(), uuid4()
    session_a = await repository.ensure_session(cat_a, requested, Corner.BEHAVIOR)
    await repository.append_message(
        cat_a, session_a, MessageRole.USER, "Mochi likes a red ball"
    )
    session_b = await repository.ensure_session(cat_b, requested, Corner.BEHAVIOR)
    assert session_b != session_a
    assert await repository.session_messages(cat_b, session_b) == []
    assert len(await repository.session_messages(cat_a, session_a)) == 1

    different_corner = await repository.ensure_session(
        cat_a, session_a, Corner.HEALTH
    )
    assert different_corner != session_a
    assert await repository.session_messages(
        cat_a, different_corner, Corner.HEALTH
    ) == []

    memory = CatMemoryService(
        repository,
        DeterministicEmbeddingProvider(),
        _NoopSummarizer(),
        summary_message_limit=20,
    )
    llm = FakeLLM()
    orchestrator = BehaviorOrchestrator(
        llm=llm,
        fast_model="fast",
        behavior_model="behavior-strong",
        health_signal_threshold=0.7,
        behavior_retriever=StaticBehaviorRetriever(),
        memory_retriever=EmptyMemoryRetriever(),
        memory_writer=memory,
        red_flags=DeterministicRedFlagChecker(),
        groundedness=CompositeGroundednessValidator(llm, "fast"),
    )
    response = await orchestrator.handle(
        BehaviorChatRequest(
            cat_id=cat_b,
            message="Why does she knock things over?",
            session_id=requested,
        )
    )
    assert response.session_id != requested
    assert (cat_b, response.session_id) in repository.sessions


def test_memory_and_retrieval_modules_never_reference_scrapbook_table() -> None:
    root = Path(__file__).parents[1] / "app"
    files = [
        *sorted((root / "memory").glob("*.py")),
        *sorted((root / "retrieval").glob("*.py")),
    ]
    for path in files:
        assert "moments" not in path.read_text(encoding="utf-8").casefold()


async def test_malformed_model_json_fails_closed_without_partial_answer() -> None:
    transport = AlwaysMalformedTransport()
    client = AnthropicStructuredClient(
        transport,
        SpendTracker(
            cap_usd=Decimal("100"),
            pricing=_zero_pricing("fast", "health-strong"),
        ),
    )
    direct = await client.generate(
        SymptomIntake,
        model="fast",
        purpose=ModelPurpose.FAST,
        system_prompt="structured",
        cache_context="",
        user_prompt="ambiguous symptom",
    )
    assert direct.value is None
    assert direct.attempts == 2

    response = await health_orchestrator(client, include_entry=True).handle(
        HealthChatRequest(
            cat_id=uuid4(),
            message="ambiguous symptom",
            intake=None,
            session_id=uuid4(),
        )
    )
    assert response.result.response_kind is TriageResponseKind.NO_RELIABLE_INFORMATION
    assert response.result.claims == []


class SchemaTransport:
    def __init__(self, *, invalid_triage_citation: bool) -> None:
        self.invalid_triage_citation = invalid_triage_citation
        self.calls: list[str] = []

    async def create_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        title = payload["output_config"]["format"]["schema"]["title"]
        self.calls.append(title)
        if title == "SymptomIntake":
            data = _default_value(SymptomIntake).model_dump(mode="json")
        elif title == "TriageResult" and self.invalid_triage_citation:
            data = {
                "severity": "routine",
                "claims": [
                    {
                        "text": "fabricated medical claim",
                        "source_entry_id": "fabricated-id",
                        "source_url": None,
                    }
                ],
                "message": "fabricated medical claim",
                "retrieved_entry_ids": [HEALTH_ENTRY.id],
                "response_kind": TriageResponseKind.TRIAGE.value,
            }
        else:
            data = _default_value(GroundednessVerdict).model_dump(mode="json")
        return {
            "content": [{"type": "text", "text": json.dumps(data)}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }

    async def count_input_tokens(self, payload: dict[str, Any]) -> int:
        return 10


class AlwaysMalformedTransport:
    async def create_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": "{not valid json"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    async def count_input_tokens(self, payload: dict[str, Any]) -> int:
        return 10


class _NoopSummarizer:
    async def summarize(self, messages: list[str]) -> MemorySummary | None:
        return None


def _zero_pricing(*models: str) -> dict[str, TokenPricing]:
    rates = TokenPricing(
        input_per_million_usd=Decimal("0"),
        output_per_million_usd=Decimal("0"),
        cache_write_per_million_usd=Decimal("0"),
        cache_read_per_million_usd=Decimal("0"),
    )
    return {model: rates for model in models}


def _default_value(output_type: type[object]) -> object | None:
    if output_type is SymptomIntake:
        return SymptomIntake(
            body_systems=[],
            duration_hours=None,
            appetite_change=AppetiteChange.UNKNOWN,
            vomiting=VomitingFrequency.UNKNOWN,
            litter_box_change=None,
            breathing_change=None,
            lethargy=None,
            free_text_residual="",
        )
    if output_type is HealthSignalCheck:
        return HealthSignalCheck(
            has_medical_signal=False, confidence=0.1, matched_terms=[]
        )
    if output_type is GroundednessVerdict:
        return GroundednessVerdict(
            passed=True, unsupported_claims=[], notes="supported"
        )
    if output_type is MemorySummary:
        return MemorySummary(
            summary="summary", salient_facts=[], covers_message_count=1
        )
    if output_type is BehaviorInterpretation:
        return BehaviorInterpretation(
            interpretation=BEHAVIOR_ENTRY.summary,
            confidence=ConfidenceLevel.GENERAL,
            reasoning="source",
            cited_entry_ids=[BEHAVIOR_ENTRY.id],
            suggested_clarifying_questions=[],
            medical_nudge=False,
        )
    return None
