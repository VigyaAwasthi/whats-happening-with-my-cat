"""Golden-dataset and zero-cost RAGAS-style metric contracts."""

from pathlib import Path
from uuid import UUID, uuid4

from app.evaluation import EvalObservation, evaluate_case, load_golden_dataset
from app.llm.client import DevelopmentStructuredClient
from app.orchestration.health import HealthOrchestrator
from app.retrieval.development import InMemoryVetKnowledgeRetriever
from app.safety.groundedness import CompositeGroundednessValidator
from app.safety.red_flags import DeterministicRedFlagChecker
from app.schemas.api import HealthChatRequest
from app.schemas.corpora import HealthEntry, SourceRef
from app.schemas.enums import BodySystem, TriageResponseKind, UrgencyTier
from app.schemas.llm import Claim
from app.schemas.memory import MemoryResult
from app.tools.contracts import MemoryRetrieverOutput

DATASET = Path(__file__).parent / "evaluation" / "golden_health.json"
CASES = load_golden_dataset(DATASET)


class EmptyMemoryRetriever:
    async def retrieve(self, request: object) -> MemoryRetrieverOutput:
        return MemoryRetrieverOutput(
            result=MemoryResult(
                cat_id=request.cat_id,
                profile_facts=["name: Mochi"],
                relevant_summaries=[],
            )
        )


class RecordingMemoryWriter:
    async def working_context(self, *_: object) -> list[str]:
        return []

    async def record_exchange(self, **kwargs: object) -> UUID:
        return kwargs["requested_session_id"]


def _portable_health_entries() -> list[HealthEntry]:
    return [
        HealthEntry(
            id=case.expected_entry_id,
            topic=case.notes,
            body_system=BodySystem.SYSTEMIC,
            aliases=[case.query],
            keywords=case.query.split(),
            summary=(
                f"{case.notes} Trusted sources recommend contacting a veterinarian."
            ),
            urgency_tier=UrgencyTier.ROUTINE,
            red_flags=["Escalate if severe or worsening."],
            when_to_see_vet="Contact a veterinarian for assessment.",
            clarifying_questions=["When did this begin?"],
            related_topics=[],
            related_conditions=[],
            sources=[
                SourceRef(
                    title=f"Golden source: {case.id}",
                    organization="Routing evaluation fixture",
                    url=f"https://example.test/{case.id}",
                )
            ],
        )
        for case in CASES
    ]


def _portable_health_orchestrator() -> HealthOrchestrator:
    llm = DevelopmentStructuredClient()
    return HealthOrchestrator(
        llm=llm,
        fast_model="development-fast",
        health_model="development-health",
        vet_retriever=InMemoryVetKnowledgeRetriever(_portable_health_entries()),
        memory_retriever=EmptyMemoryRetriever(),
        memory_writer=RecordingMemoryWriter(),
        red_flags=DeterministicRedFlagChecker(),
        groundedness=CompositeGroundednessValidator(llm, "development-fast"),
    )


def test_golden_dataset_has_reviewable_retrieval_and_answer_expectations() -> None:
    assert 15 <= len(CASES) <= 20
    assert len({case.id for case in CASES}) == len(CASES)
    assert all(case.expected_entry_id for case in CASES)
    assert all(case.good_answer_contains for case in CASES)


async def test_golden_dataset_runs_end_to_end_without_paid_services() -> None:
    orchestrator = _portable_health_orchestrator()
    for case in CASES:
        response = await orchestrator.handle(
            HealthChatRequest(
                cat_id=uuid4(),
                message=case.query,
                intake=None,
                session_id=uuid4(),
            )
        )
        assert response.result.response_kind is case.expected_response_kind, case.id
        if case.should_retrieve:
            assert case.expected_entry_id in response.result.retrieved_entry_ids, case.id
        else:
            assert response.result.retrieved_entry_ids == [], case.id
        answer = response.result.message.casefold()
        assert all(
            concept.casefold() in answer
            for concept in case.good_answer_contains
        ), case.id


def test_ragas_style_metrics_reward_supported_answer() -> None:
    case = CASES[3]
    context = (
        "Pink urine or blood in urine should be assessed by a veterinarian."
    )
    result = evaluate_case(
        case,
        EvalObservation(
            response_kind=TriageResponseKind.TRIAGE,
            retrieved_entry_ids=[case.expected_entry_id],
            contexts={case.expected_entry_id: context},
            answer="Pink urine should be assessed by a veterinarian.",
            claims=[
                Claim(
                    text="Pink urine should be assessed by a veterinarian.",
                    source_entry_id=case.expected_entry_id,
                )
            ],
        ),
    )
    assert result.response_kind_matches
    assert result.retrieval_matches
    assert result.required_concepts_present
    assert result.metrics.faithfulness == 1
    assert result.metrics.context_precision == 1
    assert result.metrics.context_recall == 1
    assert result.metrics.sentence_groundedness == 1


def test_ragas_style_metrics_expose_wrong_context_and_unsupported_claim() -> None:
    case = CASES[3]
    result = evaluate_case(
        case,
        EvalObservation(
            response_kind=TriageResponseKind.TRIAGE,
            retrieved_entry_ids=["unrelated-entry"],
            contexts={"unrelated-entry": "This context discusses coat color."},
            answer="A fabricated treatment cures this immediately.",
            claims=[
                Claim(
                    text="A fabricated treatment cures this immediately.",
                    source_entry_id="unrelated-entry",
                )
            ],
        ),
    )
    assert not result.retrieval_matches
    assert not result.required_concepts_present
    assert result.metrics.faithfulness == 0
    assert result.metrics.context_precision == 0
    assert result.metrics.context_recall == 0
    assert result.metrics.sentence_groundedness == 0
