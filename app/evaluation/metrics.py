"""Zero-cost retrieval and answer metrics suitable for regression CI.

These metrics measure faithfulness to supplied context, not whether the curated
context itself is medically correct. Corpus review remains a separate safety
process.
"""

import json
import re
from pathlib import Path

from pydantic import Field, model_validator

from app.schemas.base import ContractModel
from app.schemas.enums import TriageResponseKind
from app.schemas.llm import Claim

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "but",
    "by",
    "cat",
    "for",
    "from",
    "has",
    "have",
    "he",
    "her",
    "his",
    "i",
    "in",
    "is",
    "it",
    "my",
    "of",
    "on",
    "or",
    "she",
    "that",
    "the",
    "their",
    "this",
    "to",
    "was",
    "with",
}


class GoldenEvalCase(ContractModel):
    """One human-reviewed retrieval and response expectation."""

    id: str = Field(min_length=1, description="Stable evaluation-case identifier.")
    query: str = Field(min_length=1, description="Realistic owner query.")
    expected_entry_id: str = Field(
        min_length=1,
        description="Corpus entry that should cover the query or coded emergency.",
    )
    expected_response_kind: TriageResponseKind = Field(
        description="Expected health response disposition."
    )
    should_retrieve: bool = Field(
        description="False for deterministic emergencies that must bypass retrieval."
    )
    good_answer_contains: list[str] = Field(
        min_length=1,
        description="Human-reviewed concepts a useful answer should contain.",
    )
    notes: str = Field(min_length=1, description="Reviewer rationale.")

    @model_validator(mode="after")
    def emergency_bypasses_retrieval(self) -> "GoldenEvalCase":
        if (
            self.expected_response_kind is TriageResponseKind.EMERGENCY_CANNED
            and self.should_retrieve
        ):
            raise ValueError("deterministic emergencies must bypass retrieval")
        if (
            self.expected_response_kind is TriageResponseKind.TRIAGE
            and not self.should_retrieve
        ):
            raise ValueError("triage cases require retrieval")
        return self


class EvalObservation(ContractModel):
    """Captured output from one retrieval-and-answer run."""

    response_kind: TriageResponseKind = Field(description="Observed disposition.")
    retrieved_entry_ids: list[str] = Field(description="Ordered retrieved parent ids.")
    contexts: dict[str, str] = Field(
        description="Full parent evidence keyed by stable entry id."
    )
    answer: str = Field(min_length=1, description="Final user-facing answer.")
    claims: list[Claim] = Field(description="Structured claims before rendering.")


class RagMetrics(ContractModel):
    """Bounded RAGAS-style regression scores."""

    faithfulness: float = Field(ge=0, le=1)
    answer_relevance: float = Field(ge=0, le=1)
    context_precision: float = Field(ge=0, le=1)
    context_recall: float = Field(ge=0, le=1)
    sentence_groundedness: float = Field(ge=0, le=1)


class GoldenEvalResult(ContractModel):
    """Disposition, retrieval, content, and groundedness checks for one case."""

    case_id: str
    response_kind_matches: bool
    retrieval_matches: bool
    required_concepts_present: bool
    metrics: RagMetrics


def load_golden_dataset(path: Path) -> list[GoldenEvalCase]:
    """Load and validate the reviewable JSON dataset."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [GoldenEvalCase.model_validate(item) for item in payload]


def evaluate_case(
    case: GoldenEvalCase,
    observation: EvalObservation,
) -> GoldenEvalResult:
    """Score one captured run without making a model or network call."""
    retrieved = observation.retrieved_entry_ids
    expected_retrieved = case.expected_entry_id in retrieved
    retrieval_matches = (
        expected_retrieved if case.should_retrieve else not retrieved
    )
    answer_text = observation.answer.casefold()
    return GoldenEvalResult(
        case_id=case.id,
        response_kind_matches=(
            observation.response_kind is case.expected_response_kind
        ),
        retrieval_matches=retrieval_matches,
        required_concepts_present=all(
            concept.casefold() in answer_text
            for concept in case.good_answer_contains
        ),
        metrics=RagMetrics(
            faithfulness=_claim_faithfulness(
                observation.claims, observation.contexts
            ),
            answer_relevance=_coverage(case.query, observation.answer),
            context_precision=_context_precision(case, retrieved),
            context_recall=(
                1.0
                if not case.should_retrieve
                else float(expected_retrieved)
            ),
            sentence_groundedness=_sentence_groundedness(
                observation.answer, observation.contexts
            ),
        ),
    )


def _context_precision(case: GoldenEvalCase, retrieved: list[str]) -> float:
    if not retrieved:
        return 1.0 if not case.should_retrieve else 0.0
    relevant = sum(entry_id == case.expected_entry_id for entry_id in retrieved)
    return relevant / len(retrieved)


def _claim_faithfulness(claims: list[Claim], contexts: dict[str, str]) -> float:
    if not claims:
        return 1.0
    supported = 0
    for claim in claims:
        context = contexts.get(claim.source_entry_id)
        if context is not None and _coverage(claim.text, context) >= 0.5:
            supported += 1
    return supported / len(claims)


def _sentence_groundedness(answer: str, contexts: dict[str, str]) -> float:
    sentences = [
        sentence.strip()
        for sentence in _SENTENCE_RE.split(answer)
        if _tokens(sentence)
    ]
    if not sentences:
        return 1.0
    if not contexts:
        return 0.0
    supported = sum(
        max(_coverage(sentence, context) for context in contexts.values()) >= 0.5
        for sentence in sentences
    )
    return supported / len(sentences)


def _coverage(needle: str, haystack: str) -> float:
    terms = _tokens(needle)
    if not terms:
        return 1.0
    return len(terms & _tokens(haystack)) / len(terms)


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(text.casefold())
        if token not in _STOPWORDS
    }
