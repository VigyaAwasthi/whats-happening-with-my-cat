"""Calibration-independent behavior mode-selection regressions."""

from app.corpus_paths import resolve_corpus_dir
from app.ingestion.csv_loader import load_behavior
from app.orchestration.behavior import _entries_with_grounding_evidence
from app.tools.contracts import RankedBehaviorEntry, RetrievalScores


ENTRIES = {
    entry.id: entry
    for entry in load_behavior(
        resolve_corpus_dir() / "MASTER_behavior_corpus.csv"
    )
}


def ranked(
    entry_id: str,
    *,
    semantic: float,
    lexical: float = 0.0,
    rerank: float = 0.0,
) -> RankedBehaviorEntry:
    return RankedBehaviorEntry(
        entry_id=entry_id,
        scores=RetrievalScores(
            semantic=semantic, lexical=lexical, rerank=rerank
        ),
        entry=ENTRIES[entry_id],
    )


def select(
    query: str, entries: list[RankedBehaviorEntry]
) -> list[RankedBehaviorEntry]:
    return _entries_with_grounding_evidence(
        query, entries, minimum_coverage=0.65, minimum_terms=2
    )


def test_kneading_paraphrases_are_grounded_despite_saturated_local_scores() -> None:
    candidates = [
        ranked("kneading", semantic=0.49, rerank=0.0015),
        ranked("sleeping-on-you", semantic=0.38, rerank=0.0001),
    ]
    assert [item.entry_id for item in select(
        "she only kneads on one blanket", candidates
    )] == ["kneading"]
    assert [item.entry_id for item in select(
        "she kneads and drools on one wool blanket", candidates
    )] == ["kneading"]


def test_mirror_staring_stays_general_even_with_a_high_reranker_score() -> None:
    candidates = [
        ranked("staring", semantic=0.54, rerank=0.786),
        ranked("body-language-basics", semantic=0.46, rerank=0.01),
    ]
    assert select("my cat was staring at the mirror", candidates) == []


def test_observed_looking_and_sleeping_questions_have_source_coverage() -> None:
    looking = [
        ranked("staring", semantic=0.72, rerank=0.9),
        ranked("body-language-basics", semantic=0.50, rerank=0.2),
    ]
    sleeping = [
        ranked("sleeping-on-you", semantic=0.70, rerank=0.9),
        ranked("night-activity", semantic=0.45, rerank=0.1),
    ]
    assert select("why is he looking at me?", looking)
    assert select(
        "what does it mean when calvin is sleeping with me on the bed",
        sleeping,
    )


def test_medical_flag_text_cannot_create_grounding_or_routing_evidence() -> None:
    candidates = [
        ranked("staring", semantic=0.54, rerank=0.9),
        ranked("body-language-basics", semantic=0.45, rerank=0.1),
    ]
    assert select(
        "staring blankly at walls or into space", candidates
    ) == []


def test_reranker_and_semantic_rank_disagreement_stays_general() -> None:
    candidates = [
        ranked("kneading", semantic=0.40, rerank=0.9),
        ranked("sleeping-on-you", semantic=0.50, rerank=0.2),
    ]
    assert select("she only kneads on one blanket", candidates) == []
