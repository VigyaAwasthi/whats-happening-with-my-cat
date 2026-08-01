"""Part D response-contract and dead-configuration regressions."""

from app.config import Settings
from app.runtime_config import RuntimeSettings
from app.schemas.enums import BehaviorAnswerMode, ConfidenceLevel
from app.schemas.llm import BehaviorCitation, BehaviorInterpretation


def test_behavior_response_resolves_readable_optional_link_citation() -> None:
    answer = BehaviorInterpretation(
        interpretation="A sourced explanation.",
        answer_mode=BehaviorAnswerMode.CORPUS_GROUNDED,
        confidence=ConfidenceLevel.GENERAL,
        reasoning="The retrieved entry covers the behavior.",
        cited_entry_ids=["behavior-entry"],
        retrieved_entry_ids=["behavior-entry"],
        cited_entries=[
            BehaviorCitation(
                entry_id="behavior-entry",
                title="Readable source",
                organization="Trusted organization",
                url=None,
            )
        ],
        suggested_clarifying_questions=[],
        medical_nudge=False,
    )

    citation = answer.cited_entries[0]
    assert citation.title == "Readable source"
    assert citation.organization == "Trusted organization"
    assert citation.url is None


def test_dead_reasoning_model_setting_is_removed() -> None:
    assert "anthropic_reasoning_model" not in Settings.model_fields
    assert "anthropic_reasoning_model" not in RuntimeSettings.model_fields
