"""Behavior flow with medical handoff and code-controlled clarifying questions."""

import asyncio
import logging
import re
from collections.abc import Sequence
from contextlib import nullcontext
from uuid import UUID

from app.llm.client import ModelPurpose, StructuredLLMClient
from app.memory.service import CatMemoryService
from app.observability.collector import TraceCollector, current_trace, trace_scope
from app.observability.recording import (
    record_consensus,
    record_final_context,
    record_retrieval_stages,
)
from app.observability.repository import (
    InMemoryTraceRepository,
    TraceRepository,
    write_trace_safely,
)
from app.prompts.v1 import (
    BEHAVIOR_PROMPT_VERSION,
    BEHAVIOR_SYSTEM_PROMPT_V1,
    HEALTH_SIGNAL_SYSTEM_PROMPT_V1,
)
from app.schemas.trace import GroundednessOutcome, RetrievalConsensus
from app.safety.groundedness import CompositeGroundednessValidator
from app.safety.red_flags import DeterministicRedFlagChecker, canned_response
from app.schemas.api import BehaviorChatRequest, BehaviorChatResponse
from app.schemas.corpora import BehaviorEntry
from app.schemas.enums import BehaviorAnswerMode, ConfidenceLevel, Corner
from app.schemas.llm import (
    BehaviorCitation,
    BehaviorInterpretation,
    HealthSignalCheck,
)
from app.url_safety import safe_source_url
from app.tools.contracts import (
    BehaviorKnowledgeRetriever,
    BehaviorKnowledgeRetrieverInput,
    GroundingEvidence,
    GroundednessValidatorInput,
    MemoryRetriever,
    MemoryRetrieverInput,
    MemoryRetrieverOutput,
    RankedBehaviorEntry,
)

logger = logging.getLogger(__name__)


class BehaviorOrchestrator:
    """Looser interpretation flow whose facts and questions remain disposed by code."""

    def __init__(
        self,
        *,
        llm: StructuredLLMClient,
        fast_model: str,
        behavior_model: str,
        health_signal_threshold: float,
        health_signal_medium_threshold: float,
        behavior_grounding_min_query_coverage: float,
        behavior_grounding_min_query_terms: int,
        behavior_retriever: BehaviorKnowledgeRetriever,
        memory_retriever: MemoryRetriever,
        memory_writer: CatMemoryService,
        red_flags: DeterministicRedFlagChecker,
        groundedness: CompositeGroundednessValidator,
        traces: TraceRepository | None = None,
    ) -> None:
        self._llm = llm
        self._traces = traces or InMemoryTraceRepository()
        self._fast_model = fast_model
        self._behavior_model = behavior_model
        self._high_threshold = health_signal_threshold
        self._medium_threshold = health_signal_medium_threshold
        self._min_query_coverage = behavior_grounding_min_query_coverage
        self._min_query_terms = behavior_grounding_min_query_terms
        self._behavior = behavior_retriever
        self._memory = memory_retriever
        self._memory_writer = memory_writer
        self._red_flags = red_flags
        self._groundedness = groundedness

    async def handle(self, request: BehaviorChatRequest) -> BehaviorChatResponse:
        collector = TraceCollector(
            cat_id=request.cat_id,
            session_id=request.session_id,
            corner=Corner.BEHAVIOR,
            query=request.message,
        )
        with trace_scope(collector):
            try:
                response = await self._handle(request)
            except Exception:
                logger.exception("behavior orchestration failed closed")
                response = BehaviorChatResponse(
                    session_id=request.session_id,
                    result=_behavior_fallback(),
                    generation_id=collector.generation_id,
                )
            collector.set_outcome(
                answer_mode=response.result.answer_mode.value,
                response_text=response.result.interpretation,
                prompt_version=BEHAVIOR_PROMPT_VERSION,
            )
            # Persisting the trace is observational and comes after the answer
            # is fully formed. `write_trace_safely` cannot raise, whatever
            # repository is installed, so no trace fault can fail a request.
            await write_trace_safely(self._traces, collector.build())
            return response

    async def _handle(self, request: BehaviorChatRequest) -> BehaviorChatResponse:
        red_flag = self._red_flags.check_raw(request.message)
        if red_flag.matched_rules:
            _record_red_flag(red_flag)
            return await self._nudge(request, red_flag.canned_response_id)

        collector = current_trace()
        retrieval_timer = (
            collector.stage_timer("retrieval") if collector else nullcontext()
        )
        with retrieval_timer:
            (
                signal,
                behavior_output,
                memory_output,
                session_context,
            ) = await asyncio.gather(
                self._llm.generate(
                    HealthSignalCheck,
                    model=self._fast_model,
                    purpose=ModelPurpose.FAST,
                    system_prompt=HEALTH_SIGNAL_SYSTEM_PROMPT_V1,
                    cache_context="",
                    user_prompt=request.message,
                    max_tokens=300,
                ),
                self._behavior.retrieve(
                    BehaviorKnowledgeRetrieverInput(
                        query=request.message, cat_id=request.cat_id
                    )
                ),
                self._memory.retrieve(
                    MemoryRetrieverInput(
                        query=request.message, cat_id=request.cat_id, limit=5
                    )
                ),
                self._memory_writer.working_context(
                    request.cat_id, request.session_id, Corner.BEHAVIOR
                ),
            )
        record_retrieval_stages(behavior_output.entries)
        grounded_entries = _entries_with_grounding_evidence(
            request.message,
            behavior_output.entries,
            minimum_coverage=self._min_query_coverage,
            minimum_terms=self._min_query_terms,
        )
        record_final_context(grounded_entries)
        record_consensus(
            behavior_output.entries,
            coverage_ratio=_coverage_ratio(request.message, behavior_output.entries),
        )
        advisory_flag = _classifier_advisory_flag(
            request.message,
            grounded_entries,
            signal.value,
            high_threshold=self._high_threshold,
            medium_threshold=self._medium_threshold,
        )
        answer_mode = (
            BehaviorAnswerMode.CORPUS_GROUNDED
            if grounded_entries
            else BehaviorAnswerMode.GENERAL_KNOWLEDGE
        )
        context = _behavior_context(
            grounded_entries,
            memory_output,
            session_context,
            answer_mode=answer_mode,
        )
        generation_timer = (
            collector.stage_timer("generation") if collector else nullcontext()
        )
        with generation_timer:
            generated = await self._llm.generate(
                BehaviorInterpretation,
                model=self._behavior_model,
                purpose=ModelPurpose.BEHAVIOR,
                system_prompt=BEHAVIOR_SYSTEM_PROMPT_V1,
                cache_context=context,
                user_prompt=request.message,
                max_tokens=1200,
            )
        if generated.value is None:
            result = (
                _source_fallback(grounded_entries[0].entry)
                if grounded_entries
                else _behavior_fallback(memory_output)
            )
            if advisory_flag is not None:
                result = _with_medical_advisory(result, advisory_flag)
            session_id = await self._record(request, result)
            return BehaviorChatResponse(
            session_id=session_id,
            result=result,
            generation_id=_generation_id(),
        )

        result = generated.value
        model_requested_advisory = result.medical_nudge
        if model_requested_advisory:
            result = result.model_copy(update={"medical_nudge": False})

        if answer_mode is BehaviorAnswerMode.GENERAL_KNOWLEDGE:
            result = result.model_copy(
                update={
                    "answer_mode": BehaviorAnswerMode.GENERAL_KNOWLEDGE,
                    "confidence": ConfidenceLevel.VARIES_BY_CAT,
                    "cited_entry_ids": [],
                    "retrieved_entry_ids": [],
                    "cited_entries": [],
                    "medical_nudge": False,
                }
            )
        else:
            ids = {entry.entry_id for entry in grounded_entries}
            if (
                result.answer_mode is not BehaviorAnswerMode.CORPUS_GROUNDED
                or not result.cited_entry_ids
                or any(entry_id not in ids for entry_id in result.cited_entry_ids)
            ):
                result = _source_fallback(grounded_entries[0].entry)
            else:
                entries_by_id = {
                    ranked.entry_id: ranked.entry for ranked in grounded_entries
                }
                first_cited = entries_by_id[result.cited_entry_ids[0]]
                result = result.model_copy(
                    update={
                        "answer_mode": BehaviorAnswerMode.CORPUS_GROUNDED,
                        "confidence": first_cited.confidence,
                        "retrieved_entry_ids": sorted(ids),
                        "cited_entries": [
                            _behavior_citation(entries_by_id[entry_id])
                            for entry_id in result.cited_entry_ids
                        ],
                        "medical_nudge": False,
                    }
                )

            allowed_questions = {
                question
                for ranked in grounded_entries
                for question in ranked.entry.clarifying_questions
            }
            result = result.model_copy(
                update={
                    "suggested_clarifying_questions": [
                        question
                        for question in result.suggested_clarifying_questions
                        if question in allowed_questions
                    ]
                }
            )
            validation_timer = (
                collector.stage_timer("validation") if collector else nullcontext()
            )
            with validation_timer:
                verdict = await self._groundedness.validate(
                    GroundednessValidatorInput(
                        draft_answer=f"{result.interpretation}\n{result.reasoning}",
                        retrieved_entries=[
                            GroundingEvidence(
                                entry_id=ranked.entry_id, text=ranked.entry.summary
                            )
                            for ranked in grounded_entries
                        ],
                    )
                )
            if verdict.verdict is None or not verdict.verdict.passed:
                # Degrading to the source wording is a groundedness failure that
                # the user never sees. Without this in the trace, the answer
                # looks like an ordinary corpus-grounded response.
                _set_groundedness(GroundednessOutcome.FAILED_FELL_BACK)
                result = _source_fallback(grounded_entries[0].entry)
            else:
                _set_groundedness(GroundednessOutcome.PASSED)

        if advisory_flag is None and model_requested_advisory:
            advisory_flag = _model_advisory_flag(request.message, grounded_entries)
        if advisory_flag is not None:
            result = _with_medical_advisory(result, advisory_flag)

        session_id = await self._record(request, result)
        return BehaviorChatResponse(
            session_id=session_id,
            result=result,
            generation_id=_generation_id(),
        )

    async def _nudge(
        self, request: BehaviorChatRequest, response_id: str | None
    ) -> BehaviorChatResponse:
        """Stop only for a deterministic coded red flag, never model classification."""
        guidance = (
            canned_response(response_id).text
            if response_id is not None
            else "This may need urgent veterinary attention."
        )
        result = BehaviorInterpretation(
            interpretation=f"{guidance} Please move to the health corner now.",
            confidence=ConfidenceLevel.VARIES_BY_CAT,
            answer_mode=BehaviorAnswerMode.GENERAL_KNOWLEDGE,
            reasoning="A deterministic emergency rule matched before model inference.",
            cited_entry_ids=[],
            retrieved_entry_ids=[],
            cited_entries=[],
            suggested_clarifying_questions=[],
            medical_nudge=True,
        )
        session_id = await self._record(request, result, compact=False)
        return BehaviorChatResponse(
            session_id=session_id,
            result=result,
            generation_id=_generation_id(),
        )

    async def _record(
        self,
        request: BehaviorChatRequest,
        result: BehaviorInterpretation,
        *,
        compact: bool = True,
    ) -> UUID:
        try:
            return await self._memory_writer.record_exchange(
                cat_id=request.cat_id,
                requested_session_id=request.session_id,
                corner=Corner.BEHAVIOR,
                user_message=request.message,
                assistant_message=result.interpretation,
                compact=compact,
            )
        except Exception:
            logger.exception("behavior memory write failed without affecting response")
            return request.session_id


def _generation_id() -> UUID | None:
    """The in-flight generation's id, for echoing back to the client."""
    collector = current_trace()
    return None if collector is None else collector.generation_id


def _record_red_flag(red_flag: object) -> None:
    """Note that the deterministic gate fired, and which rule matched.

    Paired with `model_call_count == 0` in the trace, this is the auditable
    proof that an emergency short-circuited before any model was consulted.
    """
    collector = current_trace()
    if collector is None:
        return
    collector.record_red_flag(
        [str(rule) for rule in getattr(red_flag, "matched_rules", [])],
        getattr(red_flag, "canned_response_id", None),
    )


def _set_groundedness(outcome: GroundednessOutcome) -> None:
    collector = current_trace()
    if collector is not None:
        collector.set_groundedness(outcome)


def _coverage_ratio(
    query: str, entries: Sequence[RankedBehaviorEntry]
) -> float | None:
    """Deterministic term coverage of the top entry, for the trace only."""
    if not entries:
        return None
    matched, total = _query_entry_term_matches(query, entries[0].entry)
    return (matched / total) if total else None


def _behavior_context(
    entries: Sequence[RankedBehaviorEntry],
    memory_output: MemoryRetrieverOutput,
    session_context: list[str],
    *,
    answer_mode: BehaviorAnswerMode,
) -> str:
    blocks = [f"ANSWER_MODE: {answer_mode.value}"]
    blocks.extend(
        (
            f"ENTRY_ID: {ranked.entry_id}\n"
            f"SUMMARY: {ranked.entry.summary}\n"
            f"CONFIDENCE: {ranked.entry.confidence.value}\n"
            f"CLARIFYING_QUESTIONS: {' | '.join(ranked.entry.clarifying_questions)}\n"
            f"SOURCES: "
            + " | ".join(
                _behavior_source_context(
                    source.title, source.organization, source.url
                )
                for source in ranked.entry.sources
            )
        )
        for ranked in entries
    )
    memory = getattr(memory_output, "result", None)
    if memory is not None:
        blocks.append("ACTIVE CAT PROFILE:\n" + "\n".join(memory.profile_facts))
        blocks.append(
            "ACTIVE CAT MEMORY:\n"
            + "\n".join(item.summary for item in memory.relevant_summaries)
        )
    if session_context:
        blocks.append("CURRENT CAT/CORNER SESSION:\n" + "\n".join(session_context))
    return "\n\n".join(blocks)


def _source_fallback(entry: BehaviorEntry) -> BehaviorInterpretation:
    return BehaviorInterpretation(
        interpretation=entry.summary,
        answer_mode=BehaviorAnswerMode.CORPUS_GROUNDED,
        confidence=entry.confidence,
        reasoning="This wording is limited to the retrieved behavior source.",
        cited_entry_ids=[entry.id],
        retrieved_entry_ids=[entry.id],
        cited_entries=[_behavior_citation(entry)],
        suggested_clarifying_questions=entry.clarifying_questions[:2],
        medical_nudge=False,
    )


def _behavior_fallback(
    memory_output: MemoryRetrieverOutput | None = None,
) -> BehaviorInterpretation:
    memory = None if memory_output is None else memory_output.result
    profile_facts = [] if memory is None else memory.profile_facts
    profile_context = (
        ", ".join(profile_facts)
        if profile_facts
        else "the active cat's age, energy, and usual patterns"
    )
    return BehaviorInterpretation(
        interpretation=(
            "That sounds like one of those wonderfully specific cat habits. It can "
            "come from curiosity, comfort, play, or a learned little routine; the "
            "timing and what happens immediately before it are usually the best clues."
        ),
        answer_mode=BehaviorAnswerMode.GENERAL_KNOWLEDGE,
        confidence=ConfidenceLevel.VARIES_BY_CAT,
        reasoning=(
            "This is general feline understanding rather than sourced research. "
            f"The interpretation should be read in light of {profile_context}."
        ),
        cited_entry_ids=[],
        retrieved_entry_ids=[],
        cited_entries=[],
        suggested_clarifying_questions=[
            "What usually happens immediately before this?",
            "Does your cat do this at a particular time or in a particular place?",
        ],
        medical_nudge=False,
    )


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_GROUNDING_STOP_WORDS = frozenset(
    "a an and are at be been but by cat cats do does for from has have he her "
    "him his i in is it its me mean my of on one only or our she so that the "
    "their them they this to two three four five six seven eight nine ten was "
    "what when where which who why with you your specifically".split()
)
_CHANGE_CUES = frozenset(
    {
        "change",
        "changed",
        "different",
        "lately",
        "less",
        "more",
        "new",
        "started",
        "stopped",
        "sudden",
        "suddenly",
        "unusual",
        "worse",
        "worsening",
    }
)


def _entries_with_grounding_evidence(
    query: str,
    entries: Sequence[RankedBehaviorEntry],
    *,
    minimum_coverage: float,
    minimum_terms: int,
) -> list[RankedBehaviorEntry]:
    """Choose sourced mode from rank agreement plus deterministic source coverage.

    Cross-encoder score magnitudes are deliberately ignored. The top reranked parent
    must also be the semantic winner, then either win the lexical channel or cover
    enough meaningful query concepts in curated parent text. ``medical_flag`` is
    intentionally excluded: safety prose may advise, but never establishes routing
    or citation relevance.
    """
    if not entries:
        return []
    top = entries[0]
    semantic_top = max(
        entries, key=lambda entry: (entry.scores.semantic, entry.entry_id)
    )
    if top.entry_id != semantic_top.entry_id or top.scores.semantic <= 0:
        return []

    lexical_top = max(
        entries, key=lambda entry: (entry.scores.lexical, entry.entry_id)
    )
    lexical_consensus = (
        top.scores.lexical > 0 and top.entry_id == lexical_top.entry_id
    )
    matched, total = _query_entry_term_matches(query, top.entry)
    coverage = matched / total if total else 0.0
    coverage_support = (
        matched == total == 1
        or (matched >= minimum_terms and coverage >= minimum_coverage)
    )
    return [top] if lexical_consensus or coverage_support else []


def _query_entry_term_matches(query: str, entry: BehaviorEntry) -> tuple[int, int]:
    query_concepts = _content_concepts(query)
    if not query_concepts:
        return 0, 0
    source_text = " ".join(
        [
            entry.topic,
            entry.summary,
            *entry.aliases,
            *entry.keywords,
            *entry.clarifying_questions,
        ]
    )
    source_terms = set().union(
        *(_term_variants(token) for token in _TOKEN_RE.findall(source_text.casefold()))
    )
    matched = sum(bool(concept & source_terms) for concept in query_concepts)
    return matched, len(query_concepts)


def _content_concepts(text: str) -> list[set[str]]:
    return [
        _term_variants(token)
        for token in _TOKEN_RE.findall(text.casefold())
        if len(token) > 1
        and not token.isdigit()
        and token not in _GROUNDING_STOP_WORDS
    ]


def _term_variants(token: str) -> set[str]:
    variants = {token}
    if len(token) > 3 and token.endswith("s"):
        variants.add(token[:-1])
    if len(token) > 4 and token.endswith("es"):
        variants.add(token[:-2])
    if len(token) > 5 and token.endswith("ing"):
        base = token[:-3]
        variants.update({base, base + "e"})
        if len(base) > 2 and base[-1] == base[-2]:
            variants.add(base[:-1])
    if len(token) > 4 and token.endswith("ed"):
        base = token[:-2]
        variants.update({base, base + "e"})
    return variants


def _classifier_advisory_flag(
    query: str,
    entries: Sequence[RankedBehaviorEntry],
    signal: HealthSignalCheck | None,
    *,
    high_threshold: float,
    medium_threshold: float,
) -> str | None:
    if (
        signal is None
        or not signal.has_medical_signal
        or signal.confidence < medium_threshold
    ):
        return None
    flags = [flag for ranked in entries for flag in ranked.entry.medical_flag]
    if not flags:
        return None
    query_concepts = _content_concepts(query)
    query_terms = set().union(*query_concepts) if query_concepts else set()
    ranked_flags = sorted(
        flags,
        key=lambda flag: len(
            query_terms
            & set().union(
                *(
                    _term_variants(token)
                    for token in _TOKEN_RE.findall(flag.casefold())
                )
            )
        ),
        reverse=True,
    )
    best = ranked_flags[0]
    overlap = query_terms & set().union(
        *(
            _term_variants(token)
            for token in _TOKEN_RE.findall(best.casefold())
        )
    )
    if signal.confidence >= high_threshold and overlap:
        return best
    return best if _has_change_cue(query) else None


def _model_advisory_flag(
    query: str, entries: Sequence[RankedBehaviorEntry]
) -> str | None:
    if not _has_change_cue(query):
        return None
    return next(
        (flag for ranked in entries for flag in ranked.entry.medical_flag),
        None,
    )


def _has_change_cue(query: str) -> bool:
    return bool(set(_TOKEN_RE.findall(query.casefold())) & _CHANGE_CUES)


def _behavior_source_context(
    title: str, organization: str, url: str | None
) -> str:
    safe_url = safe_source_url(url)
    label = f"{title} :: {organization}"
    return f"{label} :: {safe_url}" if safe_url else label


def _behavior_citation(entry: BehaviorEntry) -> BehaviorCitation:
    """Resolve readable source metadata from the retrieved parent in code."""
    source = entry.sources[0]
    return BehaviorCitation(
        entry_id=entry.id,
        title=source.title,
        organization=source.organization,
        url=safe_source_url(source.url),
    )


def _with_medical_advisory(
    result: BehaviorInterpretation, medical_flag: str
) -> BehaviorInterpretation:
    """Append specific corpus safety context without replacing the behavior answer."""
    flag = medical_flag.strip().rstrip(".")
    note = (
        f" One thing worth watching: {flag}. If that describes what you are "
        "seeing, the health corner can look at it properly."
    )
    if note.strip() in result.interpretation:
        return result
    return result.model_copy(
        update={
            "interpretation": result.interpretation.rstrip() + note,
            "medical_nudge": True,
        }
    )
