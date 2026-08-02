"""Per-request trace collection via a context variable.

Why a context variable rather than threading a parameter through every call:
the model client and the retrievers are several layers below orchestration and
are shared by both corners. Passing a collector explicitly would change the
`StructuredLLMClient` and retriever protocols — the same typed contracts the
safety tests pin — for a purely observational concern. A context variable keeps
the contracts untouched and makes it impossible for a missed parameter to
silently drop half the trace.

`asyncio.gather` copies the current context into each task it creates. Because
the collector is a mutable object held *in* the variable, appends made inside
those tasks are visible to the caller afterwards, which is what the concurrent
retrieval/signal fan-out in both orchestrators relies on.
"""

import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator, Sequence
from decimal import Decimal
from uuid import UUID, uuid4

from app.schemas.enums import Corner
from app.schemas.trace import (
    GenerationTrace,
    GroundednessOutcome,
    ModelCallTrace,
    RetrievalConsensus,
    RetrievalStage,
    StageLatency,
    TracedRetrievalEntry,
    utc_now,
)

logger = logging.getLogger(__name__)


class TraceCollector:
    """Accumulates one generation's trace. Never raises into the request path."""

    def __init__(self, *, cat_id: UUID, session_id: UUID, corner: Corner, query: str) -> None:
        self.generation_id = uuid4()
        self._cat_id = cat_id
        self._session_id = session_id
        self._corner = corner
        self._query = query
        self._started = time.perf_counter()

        self._retrieval: list[TracedRetrievalEntry] = []
        self._consensus = RetrievalConsensus()
        self._model_calls: list[ModelCallTrace] = []
        self._latency = {"retrieval": 0.0, "generation": 0.0, "validation": 0.0}
        self._groundedness = GroundednessOutcome.NOT_APPLICABLE
        self._red_flag_rules: list[str] = []
        self._canned_response_id: str | None = None
        self._answer_mode: str | None = None
        self._response_kind: str | None = None
        self._response_text = ""
        self._prompt_version = "v1"

    # -- recording ---------------------------------------------------------

    def record_stage(
        self,
        stage: RetrievalStage,
        rows: Sequence[tuple[str, float | None, float | None, float | None]],
    ) -> None:
        """Record entry ids and scores at one retrieval stage, in rank order."""
        for rank, (entry_id, lexical, semantic, rerank) in enumerate(rows):
            self._retrieval.append(
                TracedRetrievalEntry(
                    stage=stage,
                    entry_id=entry_id,
                    rank=rank,
                    lexical=None if lexical is None else max(0.0, lexical),
                    semantic=None if semantic is None else max(0.0, semantic),
                    rerank=None if rerank is None else max(0.0, rerank),
                )
            )

    def set_consensus(self, consensus: RetrievalConsensus) -> None:
        self._consensus = consensus

    def record_model_call(self, call: ModelCallTrace) -> None:
        self._model_calls.append(call)

    def add_latency(self, stage: str, milliseconds: float) -> None:
        if stage in self._latency:
            self._latency[stage] += milliseconds

    @contextmanager
    def stage_timer(self, stage: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.add_latency(stage, (time.perf_counter() - started) * 1000)

    def set_groundedness(self, outcome: GroundednessOutcome) -> None:
        self._groundedness = outcome

    def record_red_flag(self, rules: Sequence[str], canned_response_id: str | None) -> None:
        self._red_flag_rules = list(rules)
        self._canned_response_id = canned_response_id

    def set_outcome(
        self,
        *,
        answer_mode: str | None = None,
        response_kind: str | None = None,
        response_text: str = "",
        prompt_version: str | None = None,
    ) -> None:
        if answer_mode is not None:
            self._answer_mode = answer_mode
        if response_kind is not None:
            self._response_kind = response_kind
        if response_text:
            self._response_text = response_text
        if prompt_version is not None:
            self._prompt_version = prompt_version

    # -- finalization ------------------------------------------------------

    def build(self) -> GenerationTrace:
        """Assemble the immutable record. Pure; safe to call once at the end."""
        return GenerationTrace(
            generation_id=self.generation_id,
            cat_id=self._cat_id,
            session_id=self._session_id,
            corner=self._corner,
            created_at=utc_now(),
            query=self._query,
            response_text=self._response_text,
            retrieval=list(self._retrieval),
            consensus=self._consensus,
            answer_mode=self._answer_mode,
            response_kind=self._response_kind,
            model_calls=list(self._model_calls),
            prompt_version=self._prompt_version,
            total_input_tokens=sum(call.input_tokens for call in self._model_calls),
            total_output_tokens=sum(call.output_tokens for call in self._model_calls),
            cache_read_tokens=sum(call.cache_read_tokens for call in self._model_calls),
            cache_write_tokens=sum(call.cache_write_tokens for call in self._model_calls),
            cost_usd=float(sum(Decimal(str(c.cost_usd)) for c in self._model_calls)),
            latency=StageLatency(
                retrieval_ms=self._latency["retrieval"],
                generation_ms=self._latency["generation"],
                validation_ms=self._latency["validation"],
                total_ms=(time.perf_counter() - self._started) * 1000,
            ),
            groundedness=self._groundedness,
            red_flag_fired=bool(self._red_flag_rules),
            red_flag_rules=self._red_flag_rules,
            canned_response_id=self._canned_response_id,
            model_call_count=len(self._model_calls),
        )


_current: ContextVar[TraceCollector | None] = ContextVar("generation_trace", default=None)


def current_trace() -> TraceCollector | None:
    """The collector for the in-flight generation, or None outside one."""
    return _current.get()


@contextmanager
def trace_scope(collector: TraceCollector) -> Iterator[TraceCollector]:
    """Bind a collector for the duration of one generation."""
    token = _current.set(collector)
    try:
        yield collector
    finally:
        _current.reset(token)
