"""Helpers that turn ranked retrieval output into trace rows.

Kept out of the orchestrators so the safety-critical control flow reads as it
did before tracing existed: one call per stage, no bookkeeping inline.
"""

import logging
from collections.abc import Sequence
from typing import Protocol

from app.observability.collector import current_trace
from app.schemas.trace import RetrievalConsensus, RetrievalStage

logger = logging.getLogger(__name__)


class _Scored(Protocol):
    """Structural view of RankedBehaviorEntry / RankedHealthEntry."""

    entry_id: str

    @property
    def scores(self) -> object: ...


def record_retrieval_stages(entries: Sequence[object]) -> None:
    """Record what retrieval produced, at every stage it can be observed.

    The retriever returns one ranked list, but it carries all three signals per
    entry, so the stages can be reconstructed from it:

    * ``hybrid_candidates`` - everything fusion produced, ordered as returned
    * ``post_rerank`` - the subset the cross-encoder scored, in its order
    * ``final_context`` - what was actually put in front of the model

    A retriever that skipped reranking (the reranker errored, or the
    development retriever) simply has no rerank scores, and the post-rerank
    stage is recorded empty rather than faked.
    """
    collector = current_trace()
    if collector is None:
        return
    try:
        rows = [
            (
                str(getattr(entry, "entry_id")),
                _score(entry, "lexical"),
                _score(entry, "semantic"),
                _score(entry, "rerank"),
            )
            for entry in entries
        ]
        collector.record_stage(RetrievalStage.HYBRID_CANDIDATES, rows)
        reranked = [row for row in rows if row[3] is not None]
        if reranked:
            collector.record_stage(RetrievalStage.POST_RERANK, reranked)
    except Exception:  # pragma: no cover - observability must not break requests
        logger.debug("generation trace: retrieval stages not recorded", exc_info=True)


def record_final_context(entries: Sequence[object]) -> None:
    """Record the entries that actually reached the model's context window."""
    collector = current_trace()
    if collector is None:
        return
    try:
        collector.record_stage(
            RetrievalStage.FINAL_CONTEXT,
            [
                (
                    str(getattr(entry, "entry_id")),
                    _score(entry, "lexical"),
                    _score(entry, "semantic"),
                    _score(entry, "rerank"),
                )
                for entry in entries
            ],
        )
    except Exception:  # pragma: no cover
        logger.debug("generation trace: final context not recorded", exc_info=True)


def record_consensus(
    entries: Sequence[object], *, coverage_ratio: float | None = None
) -> None:
    """Record which retrieval signals picked the same winner.

    Mode selection turns on agreement between channels rather than on score
    magnitude, so without this a "why was this general_knowledge and not
    corpus_grounded" question has no answer in the trace.
    """
    collector = current_trace()
    if collector is None:
        return
    try:
        if not entries:
            collector.set_consensus(RetrievalConsensus(coverage_ratio=coverage_ratio))
            return
        top = entries[0]
        top_id = str(getattr(top, "entry_id"))

        def leader(channel: str) -> str | None:
            scored = [
                (entry, _score(entry, channel))
                for entry in entries
                if _score(entry, channel) is not None
            ]
            if not scored:
                return None
            best = max(scored, key=lambda pair: (pair[1], str(getattr(pair[0], "entry_id"))))
            return str(getattr(best[0], "entry_id"))

        collector.set_consensus(
            RetrievalConsensus(
                top_entry_id=top_id,
                semantic_agrees=leader("semantic") == top_id,
                lexical_agrees=leader("lexical") == top_id,
                rerank_agrees=leader("rerank") == top_id,
                coverage_ratio=coverage_ratio,
            )
        )
    except Exception:  # pragma: no cover
        logger.debug("generation trace: consensus not recorded", exc_info=True)


def _score(entry: object, channel: str) -> float | None:
    scores = getattr(entry, "scores", None)
    if scores is None:
        return None
    value = getattr(scores, channel, None)
    return None if value is None else float(value)
