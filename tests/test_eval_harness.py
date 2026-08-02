"""The evaluation harness's own contracts.

Hard requirement: emergency recall must be 100% and a regression fails the
build. That assertion lives here so it runs in the ordinary `pytest` gate, not
only when somebody remembers to invoke the harness.
"""

import os

import pytest

from app.evaluation.harness import load_cases, run_routing
from app.evaluation.ragas import (
    answer_relevance,
    context_precision,
    context_recall,
    faithfulness,
    sentence_groundedness,
)


# --------------------------------------------------------------------------
# C1 — the dataset itself must be well-formed
# --------------------------------------------------------------------------


def test_golden_dataset_has_enough_cases() -> None:
    cases = load_cases()
    assert 20 <= len(cases) <= 25, f"expected 20-25 cases, found {len(cases)}"


def test_golden_case_ids_are_unique() -> None:
    ids = [case["id"] for case in load_cases()]
    assert len(ids) == len(set(ids))


def test_golden_cases_reference_real_corpus_entries() -> None:
    """A case naming an entry id that does not exist silently tests nothing."""
    os.environ.setdefault("RUNTIME_MODE", "development")
    from app.corpus_paths import resolve_corpus_dir
    from app.ingestion.csv_loader import load_behavior, load_health
    from app.runtime_config import load_runtime_settings

    directory = resolve_corpus_dir(load_runtime_settings().corpus_source_dir)
    known = {entry.id for entry in load_health(directory / "MASTER_health_corpus.csv")}
    known |= {entry.id for entry in load_behavior(directory / "MASTER_behavior_corpus.csv")}

    for case in load_cases():
        for entry_id in case.get("expect_retrieved") or []:
            assert entry_id in known, f"{case['id']} references unknown entry {entry_id!r}"


def test_golden_dataset_covers_every_required_scenario() -> None:
    """The brief names six scenario families; all must be represented."""
    cases = load_cases()
    kinds = {case.get("expect_response_kind") for case in cases}
    modes = {case.get("expect_answer_mode") for case in cases}

    assert "canned_emergency" in kinds, "clear emergencies"
    assert "triage" in kinds, "urgent but not emergency"
    assert "no_reliable_information" in kinds, "genuinely uncovered health topics"
    assert "corpus_grounded" in modes, "well-covered behavior topics"
    assert "general_knowledge" in modes, "quirky uncovered behavior"
    assert any(case.get("follow_up") for case in cases), "multi-turn follow-ups"


def test_both_corners_are_represented() -> None:
    corners = [case["corner"] for case in load_cases()]
    assert corners.count("health") >= 8
    assert corners.count("behavior") >= 8


# --------------------------------------------------------------------------
# C3 / hard requirement 3 — emergency recall is a build gate
# --------------------------------------------------------------------------


async def test_emergency_recall_is_one_hundred_percent() -> None:
    """A regression here fails the build. It is the one metric that must not move."""
    os.environ["RUNTIME_MODE"] = "development"
    from app.container import build_services
    from app.runtime_config import load_runtime_settings

    services = await build_services(load_runtime_settings())
    metrics = await run_routing(services)

    assert metrics.emergency_total >= 195, "the paraphrase corpus must not shrink"
    assert metrics.emergency_recall == 1.0, (
        f"emergency recall regressed to {metrics.emergency_recall:.4f}; "
        f"missed: {metrics.emergency_missed}"
    )
    assert metrics.emergency_missed == []


async def test_ordinary_behavior_questions_are_not_redirected() -> None:
    os.environ["RUNTIME_MODE"] = "development"
    from app.container import build_services
    from app.runtime_config import load_runtime_settings

    services = await build_services(load_runtime_settings())
    metrics = await run_routing(services)
    assert metrics.behavior_false_redirect_rate == 0.0, (
        f"behavior questions falsely redirected: {metrics.behavior_false_redirects}"
    )


# --------------------------------------------------------------------------
# C2 — metric behavior
# --------------------------------------------------------------------------


def test_faithfulness_rewards_grounded_text_and_punishes_invention() -> None:
    context = ["Cats scratch to maintain claws and mark territory with scent glands."]
    grounded = "Cats scratch to maintain their claws and to mark territory."
    invented = "Napoleon banned scratching posts throughout the French empire."
    assert faithfulness(grounded, context) > faithfulness(invented, context)
    assert faithfulness(invented, context) < 0.3


def test_faithfulness_is_zero_without_context() -> None:
    """No context means nothing can be inferable from it."""
    assert faithfulness("Cats scratch to mark territory.", []) == 0.0


def test_answer_relevance_detects_a_fluent_answer_to_another_question() -> None:
    question = "why does my cat scratch the sofa"
    on_topic = "Your cat scratches the sofa to mark territory and maintain claws."
    off_topic = "Kittens require vaccination boosters at regular intervals."
    assert answer_relevance(on_topic, question) > answer_relevance(off_topic, question)


def test_context_precision_and_recall_measure_different_failures() -> None:
    # Everything relevant retrieved, plus noise: perfect recall, poor precision.
    assert context_recall(["a", "b", "c", "d"], ["a"]) == 1.0
    assert context_precision(["a", "b", "c", "d"], ["a"]) == 0.25
    # The key entry missing: precision looks fine, recall exposes the failure.
    assert context_recall(["b"], ["a"]) == 0.0
    assert context_precision(["b"], ["b"]) == 1.0


def test_context_recall_is_the_metric_faithfulness_hides() -> None:
    """An answer can be perfectly faithful to context that lacked the answer."""
    retrieved_wrong_thing = ["dental-disease"]
    needed = ["urinary-blockage"]
    context = ["Dental disease causes bad breath and gum inflammation in cats."]
    answer = "Dental disease causes bad breath and gum inflammation."

    assert faithfulness(answer, context) > 0.9, "faithful to what it was given"
    assert context_recall(retrieved_wrong_thing, needed) == 0.0, "but the wrong context"


def test_sentence_groundedness_names_the_unsupported_sentence() -> None:
    """An average would hide one fabricated sentence among several good ones."""
    evidence = [
        ("scratching", "Cats scratch to maintain claws and mark territory."),
        ("play-needs", "Cats need daily interactive play to stay stimulated."),
    ]
    answer = (
        "Cats scratch to maintain their claws. "
        "Cats need daily interactive play. "
        "Aristotle documented this behaviour in 340 BC."
    )
    results = sentence_groundedness(answer, evidence)
    assert len(results) == 3
    unsupported = [item for item in results if not item.supported]
    assert len(unsupported) == 1
    assert "Aristotle" in unsupported[0].sentence
    # The supported ones are attributed to a specific entry, not just scored.
    assert results[0].best_entry_id == "scratching"


def test_sentence_groundedness_is_empty_for_an_empty_answer() -> None:
    assert sentence_groundedness("", [("a", "text")]) == []


@pytest.mark.parametrize(
    "answer,context,note",
    [
        (
            "Cats should be fed exactly nine times per day.",
            ["Cats should be fed exactly nine times per day."],
            "a wrong corpus fact, faithfully repeated, scores well",
        ),
    ],
)
def test_metrics_cannot_detect_a_wrong_corpus(
    answer: str, context: list[str], note: str
) -> None:
    """The documented limitation, asserted so it cannot be quietly forgotten.

    This is not a bug to fix. It is the boundary of what the harness measures,
    and it is why corpus accuracy stays a human review responsibility.
    """
    assert faithfulness(answer, context) > 0.9, note
