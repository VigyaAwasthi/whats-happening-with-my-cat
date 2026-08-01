"""Diagnose local score saturation; these scores no longer select behavior mode."""

from typing import Any

from app.corpus_paths import resolve_corpus_dir
from app.ingestion.csv_loader import load_behavior
from app.retrieval.knowledge import _behavior_document
from app.schemas.corpora import BehaviorEntry
from app.retrieval.rerank import _unit_score
from app.runtime_config import load_runtime_settings
from tests.routing.data.corpora import (
    CORPUS_GROUNDED_CALIBRATION,
    MARGINAL_BEHAVIORS,
    QUIRKY_BEHAVIORS,
)


def _score_queries(
    model: Any, entries: list[BehaviorEntry], documents: list[str], queries: list[str]
) -> list[tuple[str, str, float]]:
    rows: list[tuple[str, str, float]] = []
    for query in queries:
        scores = model.predict([(query, document) for document in documents])
        best_index = max(
            range(len(entries)), key=lambda index: float(scores[index])
        )
        rows.append(
            (
                query,
                entries[best_index].id,
                _unit_score(float(scores[best_index])),
            )
        )
    return rows


def _print_summary(label: str, scores: list[float]) -> None:
    ordered = sorted(scores)
    indexes = {
        "min": 0,
        "p25": round((len(ordered) - 1) * 0.25),
        "median": round((len(ordered) - 1) * 0.50),
        "p75": round((len(ordered) - 1) * 0.75),
        "max": len(ordered) - 1,
    }
    print(
        label,
        " ".join(f"{name}={ordered[index]:.4f}" for name, index in indexes.items()),
    )


def main() -> None:
    """Print legacy score distributions without treating them as mode decisions."""
    from sentence_transformers import CrossEncoder

    entries = load_behavior(
        resolve_corpus_dir() / "MASTER_behavior_corpus.csv"
    )
    documents = [_behavior_document(entry) for entry in entries]
    model = CrossEncoder(
        "cross-encoder/ms-marco-MiniLM-L-6-v2",
        local_files_only=True,
    )
    settings = load_runtime_settings()
    legacy_threshold = 0.35
    print(
        "configured selector: semantic/reranker rank agreement + "
        f"query coverage >= {settings.behavior_grounding_min_query_coverage:.2f} "
        f"with >= {settings.behavior_grounding_min_query_terms} matched terms"
    )
    positive_rows = _score_queries(
        model, entries, documents, list(CORPUS_GROUNDED_CALIBRATION)
    )
    quirky_rows = _score_queries(
        model, entries, documents, list(QUIRKY_BEHAVIORS)
    )
    marginal_rows = _score_queries(
        model, entries, documents, list(MARGINAL_BEHAVIORS)
    )
    positive = [
        (
            query,
            selected_id,
            CORPUS_GROUNDED_CALIBRATION[query],
            selected_id == CORPUS_GROUNDED_CALIBRATION[query],
            score,
        )
        for query, selected_id, score in positive_rows
    ]

    for candidate in (0.25, 0.35, 0.50, 0.70, 0.80, 0.85, 0.90, 0.95):
        true_positive = sum(
            correct and score >= candidate
            for _, _, _, correct, score in positive
        )
        false_negative = len(positive) - true_positive
        false_positive = sum(score >= candidate for _, _, score in quirky_rows)
        true_negative = len(quirky_rows) - false_positive
        print(
            f"{candidate:.2f}: TP={true_positive} FN={false_negative} "
            f"FP={false_positive} TN={true_negative}"
        )

    print("\nPOSITIVE SCORE DISTRIBUTION")
    for query, selected, expected, correct, score in sorted(
        positive, key=lambda row: row[-1]
    ):
        print(
            f"{score:.4f}\tselected={selected}\texpected={expected}"
            f"\tcorrect={correct}\t{query}"
        )
    _print_summary("positive summary:", [row[-1] for row in positive])

    print("\nQUIRKY SCORE DISTRIBUTION")
    for query, selected, score in sorted(quirky_rows, key=lambda row: row[-1]):
        legacy = "legacy-pass" if score >= legacy_threshold else "legacy-fail"
        print(f"{score:.4f}\t{legacy}\tselected={selected}\t{query}")
    _print_summary("quirky summary:", [row[-1] for row in quirky_rows])

    print("\nMARGINAL SCORE DISTRIBUTION")
    for query, selected, score in sorted(marginal_rows, key=lambda row: row[-1]):
        legacy = "legacy-pass" if score >= legacy_threshold else "legacy-fail"
        print(f"{score:.4f}\t{legacy}\tselected={selected}\t{query}")
    _print_summary("marginal summary:", [row[-1] for row in marginal_rows])

    print(f"\nQUIRKY CASES CLEARING LEGACY {legacy_threshold:.2f}")
    for query, selected, score in quirky_rows:
        if score >= legacy_threshold:
            print(f"{score:.4f}\t{selected}\t{query}")


if __name__ == "__main__":
    main()
