"""RAGAS-style metrics, computed separately for retrieval and generation.

## Why separated

A single "quality" number hides the thing you need to know. Faithfulness can
look excellent while the system answers confidently from context that never
contained the answer: every claim is traceable to the retrieved text, the text
was simply the wrong text. That is a *retrieval* failure wearing a generation
score. So retrieval quality (context precision, context recall) and generation
quality (faithfulness, answer relevance) are reported apart and never averaged
together.

## What these metrics CANNOT tell you

**No metric in this module can detect a factually wrong corpus.** Every one of
them measures faithfulness *to the retrieved text*. If a corpus entry states
something untrue about cats, an answer that repeats it faithfully scores 1.0 on
faithfulness and is still wrong, and the eval suite will report a clean run.

Corpus accuracy is a human responsibility. It is checked by veterinary review of
the source material, not by anything here. Treat a perfect score as "the system
is behaving as designed", never as "the answers are correct".

## Implementation note

Faithfulness and answer relevance are normally computed with an LLM judge.
Judging with a model costs money, needs an API key, and is itself noisy. This
module implements them **deterministically** — lexical entailment of claim terms
against the retrieved context — so the suite runs in CI for free and gives the
same answer twice.

The trade-off is real and worth stating: a deterministic judge is stricter and
blunter than a model. It will score a correct paraphrase below 1.0 because the
words differ. So these numbers are useful as a **relative** signal — did this
change make things worse? — and should not be read as absolute quality. The
sentence-level report below is the more actionable output.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

_STOP_WORDS = frozenset(
    "a an and are as at be been but by can cat cats do does for from had has "
    "have he her him his i if in is it its may me might my no not of on one or "
    "our she should so some that the their them then there these they this to "
    "too up us was we were what when where which who why will with would you "
    "your".split()
)


def _terms(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(text.casefold())
        if len(token) > 2 and token not in _STOP_WORDS
    }


def sentences(text: str) -> list[str]:
    """Split an answer into sentences for per-sentence attribution."""
    return [part.strip() for part in _SENTENCE_RE.split(text.strip()) if part.strip()]


@dataclass(frozen=True)
class SentenceGrounding:
    """One sentence's support, kept separate so a failure can be pointed at."""

    sentence: str
    support: float
    supported: bool
    best_entry_id: str | None


@dataclass
class GenerationMetrics:
    """How well the answer used the context it was given."""

    faithfulness: float = 0.0
    answer_relevance: float = 0.0
    sentence_groundedness: list[SentenceGrounding] = field(default_factory=list)

    @property
    def unsupported_sentences(self) -> list[SentenceGrounding]:
        """The actionable output: which sentence is unsupported, not an average.

        A mean groundedness of 0.9 across ten sentences and a single fabricated
        sentence are the same number. Only one of them is a problem, so the
        sentences are listed rather than collapsed.
        """
        return [item for item in self.sentence_groundedness if not item.supported]


@dataclass
class RetrievalMetrics:
    """Whether the right things were retrieved, independent of the answer."""

    context_precision: float = 0.0
    context_recall: float = 0.0
    retrieved_ids: list[str] = field(default_factory=list)
    expected_ids: list[str] = field(default_factory=list)

    @property
    def missing_ids(self) -> list[str]:
        return sorted(set(self.expected_ids) - set(self.retrieved_ids))


def faithfulness(answer: str, contexts: Sequence[str]) -> float:
    """Proportion of the answer's content terms inferable from the context.

    Sentences with no content terms at all (pure connective text) are skipped
    rather than counted as unfaithful.
    """
    if not answer.strip():
        return 0.0
    context_terms = set().union(*(_terms(text) for text in contexts)) if contexts else set()
    if not context_terms:
        return 0.0
    scored = [
        len(terms & context_terms) / len(terms)
        for terms in (_terms(sentence) for sentence in sentences(answer))
        if terms
    ]
    return sum(scored) / len(scored) if scored else 0.0


def answer_relevance(answer: str, question: str) -> float:
    """Whether the answer addresses the question that was asked.

    Measured as the share of the question's content terms the answer engages
    with. It catches the specific failure of a fluent, well-grounded answer to a
    question nobody asked.
    """
    question_terms = _terms(question)
    if not question_terms:
        return 0.0
    return len(question_terms & _terms(answer)) / len(question_terms)


def context_precision(
    retrieved: Sequence[str], relevant: Sequence[str]
) -> float:
    """Share of retrieved context that was actually relevant.

    Low precision means the model is reading noise, which both costs tokens and
    gives it material to wander into.
    """
    if not retrieved:
        return 0.0
    wanted = set(relevant)
    if not wanted:
        return 0.0
    return sum(1 for entry_id in retrieved if entry_id in wanted) / len(retrieved)


def context_recall(retrieved: Sequence[str], relevant: Sequence[str]) -> float:
    """Share of the needed context that retrieval actually surfaced.

    This is the metric that catches the failure faithfulness hides: an answer
    can be perfectly faithful to context that was missing the key entry.
    """
    wanted = set(relevant)
    if not wanted:
        return 1.0
    return len(wanted & set(retrieved)) / len(wanted)


def sentence_groundedness(
    answer: str,
    evidence: Sequence[tuple[str, str]],
    *,
    threshold: float = 0.5,
) -> list[SentenceGrounding]:
    """Attribute each sentence to its best supporting entry.

    Reported per sentence deliberately. An average hides the one fabricated
    sentence among nine good ones, and the fabricated sentence is the only part
    anybody needs to act on.
    """
    results: list[SentenceGrounding] = []
    for sentence in sentences(answer):
        terms = _terms(sentence)
        if not terms:
            continue
        best_id: str | None = None
        best_support = 0.0
        for entry_id, text in evidence:
            support = len(terms & _terms(text)) / len(terms)
            if support > best_support:
                best_support, best_id = support, entry_id
        results.append(
            SentenceGrounding(
                sentence=sentence,
                support=best_support,
                supported=best_support >= threshold,
                best_entry_id=best_id,
            )
        )
    return results


def evaluate_generation(
    *,
    question: str,
    answer: str,
    evidence: Sequence[tuple[str, str]],
) -> GenerationMetrics:
    """Generation-side metrics for one answer."""
    contexts = [text for _, text in evidence]
    return GenerationMetrics(
        faithfulness=faithfulness(answer, contexts),
        answer_relevance=answer_relevance(answer, question),
        sentence_groundedness=sentence_groundedness(answer, evidence),
    )


def evaluate_retrieval(
    *, retrieved: Sequence[str], expected: Sequence[str]
) -> RetrievalMetrics:
    """Retrieval-side metrics for one query."""
    return RetrievalMetrics(
        context_precision=context_precision(retrieved, expected),
        context_recall=context_recall(retrieved, expected),
        retrieved_ids=list(retrieved),
        expected_ids=list(expected),
    )
