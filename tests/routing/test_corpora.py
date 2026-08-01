"""Adversarial routing corpora that run as part of the normal pytest suite."""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.corpus_paths import resolve_corpus_dir
from app.ingestion.csv_loader import load_behavior
from app.llm.client import DevelopmentStructuredClient, ModelPurpose
from app.orchestration.behavior import BehaviorOrchestrator
from app.safety.groundedness import CompositeGroundednessValidator
from app.safety.red_flags import DeterministicRedFlagChecker
from app.schemas.api import BehaviorChatRequest
from app.schemas.enums import BehaviorAnswerMode, ConfidenceLevel
from app.schemas.llm import (
    BehaviorCitation,
    BehaviorInterpretation,
    HealthSignalCheck,
)
from app.schemas.memory import MemoryResult
from app.tools.contracts import (
    BehaviorKnowledgeRetrieverOutput,
    MemoryRetrieverOutput,
    RankedBehaviorEntry,
    RetrievalScores,
)
from .data.corpora import (
    BEHAVIOR_NEGATIVES,
    BOUNDARY_INPUTS,
    CORPUS_GROUNDED_CALIBRATION,
    EMERGENCY_PARAPHRASES,
    MARGINAL_BEHAVIORS,
    QUIRKY_BEHAVIORS,
    QUIRKY_CORPUS_MATCHES,
)


BEHAVIOR_ENTRIES = {
    entry.id: entry
    for entry in load_behavior(
        resolve_corpus_dir() / "MASTER_behavior_corpus.csv"
    )
}


class CalibratedBehaviorRetriever:
    async def retrieve(self, request: object) -> BehaviorKnowledgeRetrieverOutput:
        entry_id = QUIRKY_CORPUS_MATCHES.get(request.query)
        if entry_id is None:
            return BehaviorKnowledgeRetrieverOutput(entries=[])
        entry = BEHAVIOR_ENTRIES[entry_id]
        return BehaviorKnowledgeRetrieverOutput(
            entries=[
                RankedBehaviorEntry(
                    entry_id=entry.id,
                    scores=RetrievalScores(
                        lexical=1.0, semantic=1.0, rerank=0.9988
                    ),
                    entry=entry,
                )
            ]
        )


class PersonalizedMemoryRetriever:
    async def retrieve(self, request: object) -> MemoryRetrieverOutput:
        return MemoryRetrieverOutput(
            result=MemoryResult(
                cat_id=request.cat_id,
                profile_facts=[
                    "name: Mochi",
                    "age: 2 years",
                    "energy level: 5/5",
                    "breed: Bengal",
                    "common patterns: carries toys into the kitchen",
                ],
                relevant_summaries=[],
            )
        )


class RecordingMemoryWriter:
    async def working_context(self, *_: object) -> list[str]:
        return []

    async def record_exchange(self, **kwargs: object) -> UUID:
        return kwargs["requested_session_id"]


def _behavior_orchestrator() -> BehaviorOrchestrator:
    llm = DevelopmentStructuredClient()
    return BehaviorOrchestrator(
        llm=llm,
        fast_model="development-fast",
        behavior_model="development-strong",
        health_signal_threshold=0.70,
        health_signal_medium_threshold=0.40,
        behavior_grounding_min_query_coverage=0.65,
        behavior_grounding_min_query_terms=2,
        behavior_retriever=CalibratedBehaviorRetriever(),
        memory_retriever=PersonalizedMemoryRetriever(),
        memory_writer=RecordingMemoryWriter(),
        red_flags=DeterministicRedFlagChecker(),
        groundedness=CompositeGroundednessValidator(
            llm, "development-fast"
        ),
    )


def _emergency_cases() -> list[tuple[str, str]]:
    return [
        (rule_id, phrase)
        for rule_id, phrases in EMERGENCY_PARAPHRASES.items()
        for phrase in phrases
    ]


def test_every_safety_rule_has_at_least_fifteen_owner_phrasings() -> None:
    assert EMERGENCY_PARAPHRASES
    for rule_id, phrases in EMERGENCY_PARAPHRASES.items():
        assert len(phrases) >= 15, rule_id


def test_grounding_threshold_has_labeled_positive_and_marginal_sets() -> None:
    assert len(CORPUS_GROUNDED_CALIBRATION) >= 15
    assert all(CORPUS_GROUNDED_CALIBRATION.values())
    assert len(MARGINAL_BEHAVIORS) == 15


@pytest.mark.parametrize(
    ("expected_rule", "message"),
    _emergency_cases(),
    ids=[f"{rule}:{index}" for rule, phrases in EMERGENCY_PARAPHRASES.items() for index, _ in enumerate(phrases)],
)
def test_every_emergency_paraphrase_fires_deterministically(
    expected_rule: str, message: str
) -> None:
    result = DeterministicRedFlagChecker().check_raw(message)
    assert expected_rule in result.matched_rules, (
        f"{message!r} did not match {expected_rule}; got {result.matched_rules}"
    )


@pytest.mark.parametrize("message", BEHAVIOR_NEGATIVES)
async def test_normal_behavior_questions_never_redirect(message: str) -> None:
    assert not DeterministicRedFlagChecker().check_raw(message).matched_rules
    response = await _behavior_orchestrator().handle(
        BehaviorChatRequest(
            cat_id=uuid4(), message=message, session_id=uuid4()
        )
    )
    assert response.result.medical_nudge is False


@pytest.mark.parametrize("message", QUIRKY_BEHAVIORS)
async def test_quirky_answers_use_only_calibrated_grounding(
    message: str,
) -> None:
    assert not DeterministicRedFlagChecker().check_raw(message).matched_rules
    response = await _behavior_orchestrator().handle(
        BehaviorChatRequest(
            cat_id=uuid4(), message=message, session_id=uuid4()
        )
    )
    result = response.result
    assert result.medical_nudge is False
    expected_entry_id = QUIRKY_CORPUS_MATCHES.get(message)
    if expected_entry_id is not None:
        assert result.answer_mode is BehaviorAnswerMode.CORPUS_GROUNDED
        assert result.cited_entry_ids == [expected_entry_id]
        assert [citation.entry_id for citation in result.cited_entries] == [
            expected_entry_id
        ]
    else:
        assert result.answer_mode is BehaviorAnswerMode.GENERAL_KNOWLEDGE
        assert result.cited_entry_ids == []
        assert result.cited_entries == []
        assert len(result.interpretation) >= 80
        assert "Mochi" in result.reasoning
        assert "Bengal" in result.reasoning
        assert "2 years" in result.reasoning
        assert "5/5" in result.reasoning
        assert "carries toys into the kitchen" in result.reasoning


@pytest.mark.parametrize("message", BOUNDARY_INPUTS)
async def test_boundary_inputs_record_current_classification(
    message: str, record_property: object
) -> None:
    checker_result = DeterministicRedFlagChecker().check_raw(message)
    model_result = await DevelopmentStructuredClient().generate(
        HealthSignalCheck,
        model="development-fast",
        purpose=ModelPurpose.FAST,
        system_prompt="routing boundary observation",
        cache_context="",
        user_prompt=message,
        max_tokens=100,
    )
    assert model_result.value is not None
    classification = {
        "deterministic_rules": checker_result.matched_rules,
        "model_medical": model_result.value.has_medical_signal,
        "model_confidence": model_result.value.confidence,
    }
    record_property("routing_boundary", f"{message} => {classification}")


def test_behavior_answer_mode_validator_blocks_fabricated_grounding() -> None:
    citation = BehaviorCitation(
        entry_id="not-retrieved",
        title="A title",
        organization="An organization",
        url=None,
    )
    with pytest.raises(ValidationError, match="general_knowledge"):
        BehaviorInterpretation(
            interpretation="A general answer.",
            answer_mode=BehaviorAnswerMode.GENERAL_KNOWLEDGE,
            confidence=ConfidenceLevel.VARIES_BY_CAT,
            reasoning="General reasoning.",
            cited_entry_ids=["not-retrieved"],
            retrieved_entry_ids=[],
            cited_entries=[citation],
            suggested_clarifying_questions=[],
            medical_nudge=False,
        )
    with pytest.raises(ValidationError, match="retrieved_entry_ids"):
        BehaviorInterpretation(
            interpretation="A supposedly sourced answer.",
            answer_mode=BehaviorAnswerMode.CORPUS_GROUNDED,
            confidence=ConfidenceLevel.GENERAL,
            reasoning="Supposedly sourced reasoning.",
            cited_entry_ids=["not-retrieved"],
            retrieved_entry_ids=["different-entry"],
            cited_entries=[citation],
            suggested_clarifying_questions=[],
            medical_nudge=False,
        )
