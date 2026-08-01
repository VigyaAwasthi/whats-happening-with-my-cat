"""Focused checkpoint tests for the observed Gate 1 and behavior-routing failures."""

from pathlib import Path
from uuid import UUID, uuid4

from app.llm.client import StructuredCallResult
from app.orchestration.behavior import BehaviorOrchestrator
from app.orchestration.health import HealthOrchestrator
from app.prompts.v1 import HEALTH_SIGNAL_SYSTEM_PROMPT_V1
from app.safety.groundedness import CompositeGroundednessValidator
from app.safety.red_flags import (
    DeterministicRedFlagChecker,
    canned_response,
)
from app.schemas.api import (
    BehaviorChatRequest,
    HealthChatRequest,
    HealthChatResponse,
)
from app.schemas.corpora import BehaviorEntry, SourceRef
from app.schemas.enums import (
    BehaviorAnswerMode,
    BehaviorCategory,
    CatSex,
    ConfidenceLevel,
    TriageResponseKind,
)
from app.schemas.llm import (
    BehaviorCitation,
    BehaviorInterpretation,
    GroundednessVerdict,
    HealthSignalCheck,
)
from app.schemas.memory import MemoryResult
from app.tools.contracts import (
    BehaviorKnowledgeRetrieverOutput,
    MemoryRetrieverOutput,
    RankedBehaviorEntry,
    RetrievalScores,
)

BEHAVIOR_ENTRY = BehaviorEntry(
    id="does-my-cat-love-me",
    topic="Does my cat bond with me?",
    category=BehaviorCategory.SOCIAL,
    aliases=["does my cat love me", "cat sleeps with me"],
    keywords=["bond", "affection", "sleep"],
    summary="Cats may show social attachment through proximity and shared rest.",
    confidence=ConfidenceLevel.GENERAL,
    medical_flag=["becoming quieter or less interactive than usual"],
    clarifying_questions=["Does your cat choose to settle beside you at other times?"],
    related_topics=[],
    sources=[
        SourceRef(
            title="Feline social behavior",
            organization="Trusted behavior source",
            url="https://example.test/feline-social-behavior",
        )
    ],
)


class RecordingLLM:
    """Schema-keyed model stub that exposes every attempted model call."""

    def __init__(self, signal: HealthSignalCheck) -> None:
        self.signal = signal
        self.calls: list[type[object]] = []

    async def generate(
        self,
        output_type: type[object],
        **_: object,
    ) -> StructuredCallResult:
        self.calls.append(output_type)
        if output_type is HealthSignalCheck:
            return StructuredCallResult(value=self.signal)
        if output_type is BehaviorInterpretation:
            source = BEHAVIOR_ENTRY.sources[0]
            return StructuredCallResult(
                value=BehaviorInterpretation(
                    interpretation=BEHAVIOR_ENTRY.summary,
                    answer_mode=BehaviorAnswerMode.CORPUS_GROUNDED,
                    confidence=BEHAVIOR_ENTRY.confidence,
                    reasoning="The retrieved entry directly covers this behavior.",
                    cited_entry_ids=[BEHAVIOR_ENTRY.id],
                    retrieved_entry_ids=[BEHAVIOR_ENTRY.id],
                    cited_entries=[
                        BehaviorCitation(
                            entry_id=BEHAVIOR_ENTRY.id,
                            title=source.title,
                            organization=source.organization,
                            url=source.url,
                        )
                    ],
                    suggested_clarifying_questions=[],
                    medical_nudge=False,
                )
            )
        if output_type is GroundednessVerdict:
            return StructuredCallResult(
                value=GroundednessVerdict(
                    passed=True, unsupported_claims=[], notes="supported"
                )
            )
        raise AssertionError(f"unexpected model output type: {output_type}")


class NeverCallLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, *_: object, **__: object) -> StructuredCallResult:
        self.calls += 1
        raise AssertionError("the deterministic emergency gate must short-circuit")


class StaticBehaviorRetriever:
    async def retrieve(self, _: object) -> BehaviorKnowledgeRetrieverOutput:
        return BehaviorKnowledgeRetrieverOutput(
            entries=[
                RankedBehaviorEntry(
                    entry_id=BEHAVIOR_ENTRY.id,
                    scores=RetrievalScores(
                        lexical=1.0, semantic=1.0, rerank=0.9
                    ),
                    entry=BEHAVIOR_ENTRY,
                )
            ]
        )


class ProfileMemoryRetriever:
    async def retrieve(self, request: object) -> MemoryRetrieverOutput:
        return MemoryRetrieverOutput(
            result=MemoryResult(
                cat_id=request.cat_id,
                profile_facts=[
                    "name: Mochi",
                    "age: 2 years",
                    "energy level: 5/5",
                    "breed: Bengal",
                    "common patterns: follows people from room to room",
                ],
                relevant_summaries=[],
            )
        )


class RecordingMemoryWriter:
    def __init__(self) -> None:
        self.record_calls: list[dict[str, object]] = []

    async def working_context(self, *_: object) -> list[str]:
        return []

    async def record_exchange(self, **kwargs: object) -> UUID:
        self.record_calls.append(kwargs)
        return kwargs["requested_session_id"]


class NeverRetrieve:
    async def retrieve(self, _: object) -> object:
        raise AssertionError("retrieval must not run after an emergency match")


def _behavior(llm: RecordingLLM) -> BehaviorOrchestrator:
    return BehaviorOrchestrator(
        llm=llm,
        fast_model="fast",
        behavior_model="strong",
        health_signal_threshold=0.70,
        health_signal_medium_threshold=0.40,
        behavior_grounding_min_query_coverage=0.65,
        behavior_grounding_min_query_terms=2,
        behavior_retriever=StaticBehaviorRetriever(),
        memory_retriever=ProfileMemoryRetriever(),
        memory_writer=RecordingMemoryWriter(),
        red_flags=DeterministicRedFlagChecker(),
        groundedness=CompositeGroundednessValidator(llm, "fast"),
    )


async def test_unusual_breathing_is_gate_one_emergency_without_model_call() -> None:
    llm = NeverCallLLM()
    memory_writer = RecordingMemoryWriter()
    orchestrator = HealthOrchestrator(
        llm=llm,
        fast_model="fast",
        health_model="strong",
        vet_retriever=NeverRetrieve(),
        memory_retriever=NeverRetrieve(),
        memory_writer=memory_writer,
        red_flags=DeterministicRedFlagChecker(),
        groundedness=None,
    )
    response = await orchestrator.handle(
        HealthChatRequest(
            cat_id=uuid4(),
            message="unusual breathing",
            intake=None,
            session_id=uuid4(),
        )
    )
    assert response.result.response_kind is TriageResponseKind.EMERGENCY_CANNED
    assert response.result.message.startswith("Difficulty")
    assert llm.calls == 0
    assert memory_writer.record_calls[0]["compact"] is False


async def test_sleeping_with_owner_is_behavior_not_medical_redirect() -> None:
    llm = RecordingLLM(
        HealthSignalCheck(
            has_medical_signal=False, confidence=0.97, matched_terms=[]
        )
    )
    response = await _behavior(llm).handle(
        BehaviorChatRequest(
            cat_id=uuid4(),
            message="why does my cat sleep with me at night?",
            session_id=uuid4(),
        )
    )
    assert response.result.medical_nudge is False
    assert response.result.answer_mode is BehaviorAnswerMode.CORPUS_GROUNDED
    assert BehaviorInterpretation in llm.calls


async def test_medium_medical_confidence_answers_then_adds_specific_advisory() -> None:
    llm = RecordingLLM(
        HealthSignalCheck(
            has_medical_signal=True,
            confidence=0.55,
            matched_terms=["possible change"],
        )
    )
    response = await _behavior(llm).handle(
        BehaviorChatRequest(
            cat_id=uuid4(),
            message="why has she been a little quieter lately?",
            session_id=uuid4(),
        )
    )
    assert response.result.medical_nudge is True
    assert BEHAVIOR_ENTRY.summary in response.result.interpretation
    assert "becoming quieter or less interactive than usual" in (
        response.result.interpretation
    )
    assert "That could be medical rather than behavioral" not in (
        response.result.interpretation
    )
    assert BehaviorInterpretation in llm.calls


def test_classifier_prompt_covers_confusable_change_language() -> None:
    prompt = HEALTH_SIGNAL_SYSTEM_PROMPT_V1
    assert "sleep with me at night" in prompt
    assert "sleep so much all of a sudden" in prompt
    assert "eat grass" in prompt
    assert "stopped eating" in prompt
    assert "licking a bald patch" in prompt
    assert "hiding more than usual" in prompt


def test_respiratory_bare_mention_and_mobile_apostrophes_are_normalized() -> None:
    checker = DeterministicRedFlagChecker()
    assert "breathing_difficulty" in checker.check_raw("breathin funny").matched_rules
    assert "urinary_obstruction" in checker.check_raw("she can’t pee").matched_rules
    assert "urinary_obstruction" in checker.check_raw("she cant pee").matched_rules
    assert "seizure" in checker.check_raw("possible siezure").matched_rules
    assert "toxin_ingestion" in checker.check_raw("maybe poisen").matched_rules


def test_respiratory_asymmetry_is_documented_in_code() -> None:
    source = (
        Path(__file__).parents[2] / "app" / "safety" / "red_flags.py"
    ).read_text(encoding="utf-8")
    assert "cost of a false negative can be death" in source
    assert "bare respiratory mention" in source


async def _urinary_response(
    cat_sex: CatSex,
) -> tuple[HealthChatResponse, NeverCallLLM]:
    llm = NeverCallLLM()
    orchestrator = HealthOrchestrator(
        llm=llm,
        fast_model="fast",
        health_model="strong",
        vet_retriever=NeverRetrieve(),
        memory_retriever=NeverRetrieve(),
        memory_writer=RecordingMemoryWriter(),
        red_flags=DeterministicRedFlagChecker(),
        groundedness=None,
    )
    response = await orchestrator.handle(
        HealthChatRequest(
            cat_id=uuid4(),
            message="straining to pee and nothing comes out",
            intake=None,
            session_id=uuid4(),
        ),
        cat_sex=cat_sex,
    )
    return response, llm


async def test_male_urinary_emergency_adds_stronger_framing() -> None:
    response, llm = await _urinary_response(CatSex.MALE)
    canned = canned_response("urinary_obstruction")
    assert canned.male_addendum is not None
    assert response.result.response_kind is TriageResponseKind.EMERGENCY_CANNED
    assert response.result.message == f"{canned.text} {canned.male_addendum}"
    assert llm.calls == 0


async def test_female_urinary_emergency_keeps_full_base_framing() -> None:
    response, llm = await _urinary_response(CatSex.FEMALE)
    canned = canned_response("urinary_obstruction")
    assert response.result.response_kind is TriageResponseKind.EMERGENCY_CANNED
    assert response.result.message == canned.text
    assert llm.calls == 0


async def test_unknown_sex_urinary_emergency_keeps_full_base_framing() -> None:
    response, llm = await _urinary_response(CatSex.UNKNOWN)
    canned = canned_response("urinary_obstruction")
    assert response.result.response_kind is TriageResponseKind.EMERGENCY_CANNED
    assert response.result.message == canned.text
    assert llm.calls == 0


async def test_urinary_base_text_is_byte_identical_for_every_sex() -> None:
    canned = canned_response("urinary_obstruction")
    base = canned.text.encode("utf-8")
    responses = [
        (await _urinary_response(cat_sex))[0]
        for cat_sex in (CatSex.MALE, CatSex.FEMALE, CatSex.UNKNOWN)
    ]
    prefixes = [
        response.result.message.encode("utf-8")[: len(base)]
        for response in responses
    ]
    assert prefixes == [base, base, base]


async def test_every_urinary_sex_path_bypasses_all_model_calls() -> None:
    calls = []
    for cat_sex in (CatSex.MALE, CatSex.FEMALE, CatSex.UNKNOWN):
        response, llm = await _urinary_response(cat_sex)
        assert response.result.response_kind is TriageResponseKind.EMERGENCY_CANNED
        calls.append(llm.calls)
    assert calls == [0, 0, 0]


async def test_female_urinary_response_contains_no_reassurance_language() -> None:
    response, _ = await _urinary_response(CatSex.FEMALE)
    message = response.result.message.casefold()
    reassurance = ("less likely", "unlikely", "probably", "wait")
    assert all(term not in message for term in reassurance)


async def test_fuzzy_medical_signal_never_replaces_harmless_chat_answers() -> None:
    messages = (
        "why is he looking at me?",
        "what does it mean when calvin is sleeping with me on the bed",
    )
    for message in messages:
        llm = RecordingLLM(
            HealthSignalCheck(
                has_medical_signal=True,
                confidence=0.99,
                matched_terms=["model-only fuzzy signal"],
            )
        )
        response = await _behavior(llm).handle(
            BehaviorChatRequest(
                cat_id=uuid4(), message=message, session_id=uuid4()
            )
        )
        assert BehaviorInterpretation in llm.calls
        assert BEHAVIOR_ENTRY.summary in response.result.interpretation
        assert "behavior interpretation stopped" not in response.result.reasoning
        assert response.result.medical_nudge is False
