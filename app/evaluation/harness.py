"""Run the golden dataset and the routing corpora, and report metrics.

    python -m app.evaluation.harness                 # human-readable report
    python -m app.evaluation.harness --json          # machine-readable
    python -m app.evaluation.harness --record        # append to history

Two independent things are measured, and they answer different questions:

* **Routing metrics** (`--routing`) run over the existing paraphrase corpora and
  are fully deterministic: emergency recall, behavior false-redirect rate, and
  the grounding rate. These are real numbers and are the CI gate.
* **Golden-dataset metrics** run the actual orchestrators over 24 curated cases
  and report RAGAS-style retrieval and generation quality.

The suite runs against `RUNTIME_MODE=development`, which uses the deterministic
stub model client. That makes routing, retrieval, and safety-gate behavior
genuinely measured — those paths contain no model judgement. It also means
**faithfulness and answer relevance describe the stub's wording, not a real
model's**, so those two numbers are a regression tripwire rather than a quality
read. Point this at a real model to get a quality read; see EVALUATION.md.
"""

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import yaml

GOLDEN_PATH = Path(__file__).resolve().parents[2] / "tests" / "eval" / "golden.yaml"
HISTORY_PATH = Path(__file__).resolve().parents[2] / "tests" / "eval" / "history.jsonl"


@dataclass
class CaseResult:
    """One golden case's outcome."""

    id: str
    corner: str
    passed: bool
    skipped: bool = False
    failures: list[str] = field(default_factory=list)
    context_precision: float = 0.0
    context_recall: float = 0.0
    faithfulness: float = 0.0
    answer_relevance: float = 0.0
    unsupported_sentences: list[str] = field(default_factory=list)
    answer_mode: str | None = None
    response_kind: str | None = None
    model_calls: int = 0
    retrieved: list[str] = field(default_factory=list)
    expected_count: int = 0


@dataclass
class RoutingMetrics:
    """Deterministic routing behavior over the paraphrase corpora."""

    emergency_recall: float = 0.0
    emergency_total: int = 0
    emergency_missed: list[str] = field(default_factory=list)
    behavior_false_redirect_rate: float = 0.0
    behavior_total: int = 0
    behavior_false_redirects: list[str] = field(default_factory=list)
    grounding_rate: float = 0.0
    grounding_total: int = 0
    grounded_queries: list[str] = field(default_factory=list)
    ungrounded_queries: list[str] = field(default_factory=list)


def load_cases() -> list[dict[str, Any]]:
    """Read the golden dataset."""
    data = yaml.safe_load(GOLDEN_PATH.read_text(encoding="utf-8"))
    return list(data["cases"])


async def _build_services():  # type: ignore[no-untyped-def]
    os.environ.setdefault("RUNTIME_MODE", "development")
    from app.container import build_services
    from app.runtime_config import load_runtime_settings

    return await build_services(load_runtime_settings())


_CORPUS_TEXT: dict[str, str] | None = None


def corpus_text() -> dict[str, str]:
    """Map every corpus entry id to its real text, cached for the run.

    Faithfulness has to be scored against what the entries actually *say*.
    Scoring against the entry id — which is what a first pass at this did —
    makes every answer look 0.0 faithful and the metric worthless.
    """
    global _CORPUS_TEXT
    if _CORPUS_TEXT is None:
        from app.corpus_paths import resolve_corpus_dir
        from app.ingestion.csv_loader import load_behavior, load_health
        from app.runtime_config import load_runtime_settings

        directory = resolve_corpus_dir(load_runtime_settings().corpus_source_dir)
        entries = load_health(directory / "MASTER_health_corpus.csv") + load_behavior(
            directory / "MASTER_behavior_corpus.csv"
        )
        _CORPUS_TEXT = {
            entry.id: " ".join(
                [entry.topic, entry.summary, *entry.aliases, *entry.keywords]
            )
            for entry in entries
        }
    return _CORPUS_TEXT


async def run_golden(services: Any) -> list[CaseResult]:
    """Execute every golden case through the real orchestrators."""
    from app.evaluation.ragas import evaluate_generation, evaluate_retrieval
    from app.schemas.api import BehaviorChatRequest, HealthChatRequest
    from app.schemas.domain import CatAge, CatProfile, CatTheme, CatWeight
    from app.schemas.enums import AgeUnit, CatSex, EnergyLevel, WeightUnit
    from app.schemas.trace import RetrievalStage

    development_stub = os.getenv("RUNTIME_MODE", "").casefold() == "development"

    results: list[CaseResult] = []
    for case in load_cases():
        # The development retriever returns entries for any query, so it can
        # never produce the empty result that `no_reliable_information` depends
        # on. Those cases are skipped here rather than relaxed — relaxing them
        # would delete the assertion that matters most about the health corner.
        # They are exercised against the deployed system by
        # `scripts/verify_deployment.py`.
        if case.get("requires_real_retrieval") and development_stub:
            results.append(
                CaseResult(
                    id=case["id"],
                    corner=case["corner"],
                    passed=True,
                    skipped=True,
                    failures=[],
                )
            )
            continue
        cat_id = uuid4()
        profile = case.get("cat_profile") or {}
        # Register the cat so retrieval and memory are cat-scoped as in production.
        services.repository.cats[cat_id] = CatProfile(
            id=cat_id,
            account_id=services.repository.account.id,
            name="EvalCat",
            age=CatAge(value=float(profile.get("age", 3)), unit=AgeUnit.YEARS),
            breed=profile.get("breed"),
            sex=CatSex.UNKNOWN,
            weight=CatWeight(value=9, unit=WeightUnit.POUNDS),
            energy_level=EnergyLevel(3),
            common_patterns="",
            known_conditions=list(profile.get("conditions") or []),
            photo_references=[],
            theme=CatTheme(primary_color="#E43D12", accent_color="#E43D12"),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        session_id = uuid4()
        queries = [case["query"]]
        if case.get("follow_up"):
            queries.append(case["follow_up"])

        response = None
        for query in queries:
            if case["corner"] == "health":
                response = await services.health.handle(
                    HealthChatRequest(
                        cat_id=cat_id, message=query, intake=None, session_id=session_id
                    )
                )
            else:
                response = await services.behavior.handle(
                    BehaviorChatRequest(
                        cat_id=cat_id, message=query, session_id=session_id
                    )
                )
            session_id = response.session_id

        assert response is not None
        trace = await services.traces.get(response.generation_id)
        retrieved = (
            trace.entries_at(RetrievalStage.HYBRID_CANDIDATES) if trace else []
        )
        expected = list(case.get("expect_retrieved") or [])

        if case["corner"] == "health":
            answer = response.result.message
            answer_mode = None
            response_kind = response.result.response_kind.value
        else:
            answer = response.result.interpretation
            answer_mode = response.result.answer_mode.value
            response_kind = None

        final_context = (
            trace.entries_at(RetrievalStage.FINAL_CONTEXT) if trace else []
        )
        # Recall asks "did retrieval surface it at all", so it is scored over the
        # candidate pool. Precision asks "was what we actually fed the model
        # relevant", so it is scored over the final context. Scoring precision
        # over the whole candidate pool would punish having a wide pool, which
        # is the pool's job.
        retrieval = evaluate_retrieval(retrieved=retrieved, expected=expected)
        precision_only = evaluate_retrieval(retrieved=final_context, expected=expected)
        retrieval.context_precision = precision_only.context_precision

        texts = corpus_text()
        evidence = [
            (entry_id, texts.get(entry_id, entry_id.replace("-", " ")))
            for entry_id in final_context
        ]
        generation = evaluate_generation(
            question=queries[-1], answer=answer, evidence=evidence
        )

        failures: list[str] = []
        lowered = answer.casefold()

        expected_kind = case.get("expect_response_kind")
        if expected_kind == "canned_emergency":
            severity = getattr(response.result, "severity", None)
            if severity is None or severity.value != "emergency":
                failures.append(f"expected emergency severity, got {severity}")
        elif expected_kind and response_kind != expected_kind:
            failures.append(f"response_kind {response_kind!r} != {expected_kind!r}")

        expected_severity = case.get("expect_severity")
        if expected_severity:
            severity = getattr(response.result, "severity", None)
            if severity is None or severity.value != expected_severity:
                failures.append(f"severity {severity} != {expected_severity}")

        expected_mode = case.get("expect_answer_mode")
        if expected_mode and answer_mode != expected_mode:
            failures.append(f"answer_mode {answer_mode!r} != {expected_mode!r}")

        if case.get("expect_no_model_call"):
            calls = trace.model_call_count if trace else -1
            if calls != 0:
                failures.append(f"expected no model call, saw {calls}")

        for needle in case.get("answer_should_contain") or []:
            if needle.casefold() not in lowered:
                failures.append(f"answer missing {needle!r}")
        for needle in case.get("answer_should_not_contain") or []:
            if needle.casefold() in lowered:
                failures.append(f"answer contains forbidden {needle!r}")

        results.append(
            CaseResult(
                id=case["id"],
                corner=case["corner"],
                passed=not failures,
                failures=failures,
                context_precision=retrieval.context_precision,
                context_recall=retrieval.context_recall,
                faithfulness=generation.faithfulness,
                answer_relevance=generation.answer_relevance,
                unsupported_sentences=[
                    item.sentence for item in generation.unsupported_sentences
                ],
                answer_mode=answer_mode,
                response_kind=response_kind,
                model_calls=trace.model_call_count if trace else 0,
                retrieved=retrieved[:8],
                expected_count=len(expected),
            )
        )
    return results


async def run_routing(services: Any) -> RoutingMetrics:
    """Deterministic routing metrics over the existing paraphrase corpora."""
    from app.schemas.api import BehaviorChatRequest, HealthChatRequest
    from app.schemas.domain import CatAge, CatProfile, CatTheme, CatWeight
    from app.schemas.enums import AgeUnit, BehaviorAnswerMode, CatSex, EnergyLevel, WeightUnit
    from tests.routing.data.corpora import (
        BEHAVIOR_NEGATIVES,
        EMERGENCY_PARAPHRASES,
        QUIRKY_BEHAVIORS,
    )
    from app.safety.red_flags import DeterministicRedFlagChecker

    metrics = RoutingMetrics()
    checker = DeterministicRedFlagChecker()

    # --- emergency recall: must be 100%. This is the CI gate. --------------
    phrases = [
        (rule, phrase)
        for rule, group in EMERGENCY_PARAPHRASES.items()
        for phrase in group
    ]
    metrics.emergency_total = len(phrases)
    for rule, phrase in phrases:
        if not checker.check_raw(phrase).matched_rules:
            metrics.emergency_missed.append(f"{rule}: {phrase}")
    metrics.emergency_recall = (
        (metrics.emergency_total - len(metrics.emergency_missed))
        / metrics.emergency_total
        if metrics.emergency_total
        else 0.0
    )

    # --- behavior false-redirect rate --------------------------------------
    # An ordinary behavior question must not be diverted to the emergency path.
    metrics.behavior_total = len(BEHAVIOR_NEGATIVES)
    for phrase in BEHAVIOR_NEGATIVES:
        if checker.check_raw(phrase).matched_rules:
            metrics.behavior_false_redirects.append(phrase)
    metrics.behavior_false_redirect_rate = (
        len(metrics.behavior_false_redirects) / metrics.behavior_total
        if metrics.behavior_total
        else 0.0
    )

    # --- grounding rate: the corpus-coverage signal -------------------------
    cat_id = uuid4()
    services.repository.cats[cat_id] = CatProfile(
        id=cat_id,
        account_id=services.repository.account.id,
        name="RoutingCat",
        age=CatAge(value=4, unit=AgeUnit.YEARS),
        breed=None,
        sex=CatSex.UNKNOWN,
        weight=CatWeight(value=9, unit=WeightUnit.POUNDS),
        energy_level=EnergyLevel(3),
        common_patterns="",
        known_conditions=[],
        photo_references=[],
        theme=CatTheme(primary_color="#E43D12", accent_color="#E43D12"),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    probes = list(BEHAVIOR_NEGATIVES) + list(QUIRKY_BEHAVIORS)
    metrics.grounding_total = len(probes)
    for phrase in probes:
        response = await services.behavior.handle(
            BehaviorChatRequest(
                cat_id=cat_id, message=phrase, session_id=uuid4()
            )
        )
        if response.result.answer_mode is BehaviorAnswerMode.CORPUS_GROUNDED:
            metrics.grounded_queries.append(phrase)
        else:
            metrics.ungrounded_queries.append(phrase)
    metrics.grounding_rate = (
        len(metrics.grounded_queries) / metrics.grounding_total
        if metrics.grounding_total
        else 0.0
    )
    return metrics


def _summarize(results: list[CaseResult], routing: RoutingMetrics) -> dict[str, Any]:
    def mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    # Retrieval metrics only mean something for cases that named expected ids.
    live = [r for r in results if not r.skipped]
    scored = [r for r in live if r.retrieved]
    with_expectations = [r for r in live if r.expected_count]
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cases_total": len(results),
        "cases_run": len(live),
        "cases_skipped": sum(1 for r in results if r.skipped),
        "cases_passed": sum(1 for r in live if r.passed),
        "retrieval": {
            "context_precision": mean([r.context_precision for r in scored]),
            "context_recall": mean([r.context_recall for r in with_expectations]),
        },
        "generation": {
            "faithfulness": mean([r.faithfulness for r in live]),
            "answer_relevance": mean([r.answer_relevance for r in live]),
            "sentences_unsupported": sum(
                len(r.unsupported_sentences) for r in live
            ),
        },
        "routing": {
            "emergency_recall": routing.emergency_recall,
            "emergency_total": routing.emergency_total,
            "behavior_false_redirect_rate": routing.behavior_false_redirect_rate,
            "grounding_rate": routing.grounding_rate,
            "grounding_total": routing.grounding_total,
        },
    }


async def _run(args: argparse.Namespace) -> int:
    services = await _build_services()
    routing = await run_routing(services)
    results = [] if args.routing else await run_golden(services)
    summary = _summarize(results, routing)

    if args.json:
        print(
            json.dumps(
                {
                    "summary": summary,
                    "cases": [asdict(r) for r in results],
                    "routing_detail": asdict(routing),
                },
                indent=2,
            )
        )
    else:
        _print_report(results, routing, summary)

    if args.record:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(summary) + "\n")
        print(f"\nrecorded to {HISTORY_PATH}")

    # Only emergency recall fails the build. Quality metrics move for legitimate
    # reasons and gating on them trains people to ignore the gate.
    if routing.emergency_recall < 1.0:
        print(
            f"\nFAIL: emergency recall {routing.emergency_recall:.4f} < 1.0 — "
            f"{len(routing.emergency_missed)} paraphrase(s) missed"
        )
        for missed in routing.emergency_missed[:20]:
            print(f"  - {missed}")
        return 1
    failed = [r for r in results if not r.passed and not r.skipped]
    if failed and args.strict:
        print(f"\nFAIL (--strict): {len(failed)} golden case(s) failed")
        return 1
    return 0


def _print_report(
    results: list[CaseResult], routing: RoutingMetrics, summary: dict[str, Any]
) -> None:
    print("=" * 74)
    print("ROUTING METRICS (deterministic — these are the real numbers)")
    print("=" * 74)
    status = "PASS" if routing.emergency_recall >= 1.0 else "FAIL"
    print(
        f"  emergency recall            {routing.emergency_recall:>7.2%}  "
        f"({routing.emergency_total} paraphrases)  [{status}, must be 100%]"
    )
    print(
        f"  behavior false-redirect     {routing.behavior_false_redirect_rate:>7.2%}  "
        f"({routing.behavior_total} negatives)"
    )
    print(
        f"  grounding rate              {routing.grounding_rate:>7.2%}  "
        f"({len(routing.grounded_queries)}/{routing.grounding_total} reached corpus_grounded)"
    )
    if routing.emergency_missed:
        print("\n  MISSED EMERGENCIES:")
        for missed in routing.emergency_missed:
            print(f"    - {missed}")
    if routing.behavior_false_redirects:
        print("\n  false redirects:")
        for phrase in routing.behavior_false_redirects:
            print(f"    - {phrase}")

    if not results:
        return

    print("")
    print("=" * 74)
    print("GOLDEN DATASET")
    print("=" * 74)
    print(
        f"  cases passed  {summary['cases_passed']}/{summary['cases_run']}"
        + (
            f"   ({summary['cases_skipped']} skipped: need real retrieval)"
            if summary["cases_skipped"]
            else ""
        )
    )
    print("")
    print("  retrieval (measured — no model judgement involved)")
    print(f"    context precision        {summary['retrieval']['context_precision']:>7.3f}")
    print(f"    context recall           {summary['retrieval']['context_recall']:>7.3f}")
    print("")
    print("  generation (deterministic judge over the development stub —")
    print("              a regression tripwire, NOT an absolute quality read)")
    print(f"    faithfulness             {summary['generation']['faithfulness']:>7.3f}")
    print(f"    answer relevance         {summary['generation']['answer_relevance']:>7.3f}")
    print(f"    unsupported sentences    {summary['generation']['sentences_unsupported']:>7}")

    failed = [r for r in results if not r.passed and not r.skipped]
    if failed:
        print("")
        print(f"  FAILING CASES ({len(failed)}):")
        for result in failed:
            print(f"    {result.id} [{result.corner}]")
            for failure in result.failures:
                print(f"      - {failure}")
    print("")
    print("  NOTE: no metric above can detect a factually wrong corpus. They all")
    print("  measure faithfulness TO the retrieved text. Corpus accuracy is a")
    print("  human responsibility. See app/evaluation/ragas.py.")


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the evaluation."""
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation.harness",
        description="Run the golden dataset and routing metrics.",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--record", action="store_true", help="append to history.jsonl")
    parser.add_argument(
        "--routing", action="store_true", help="routing metrics only (fast)"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="also fail on golden-case failures, not just emergency recall",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
