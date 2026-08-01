"""Offline evaluation contracts and deterministic RAGAS-style metrics."""

from app.evaluation.metrics import (
    EvalObservation,
    GoldenEvalCase,
    GoldenEvalResult,
    RagMetrics,
    evaluate_case,
    load_golden_dataset,
)

__all__ = [
    "EvalObservation",
    "GoldenEvalCase",
    "GoldenEvalResult",
    "RagMetrics",
    "evaluate_case",
    "load_golden_dataset",
]
