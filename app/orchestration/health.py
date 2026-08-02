"""Strict health flow: code gates, retrieval lock, then grounded generation."""

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
from app.orchestration.common import (
    VET_REFERRAL_LINE,
    no_reliable_information,
    record_health_safely,
)
from app.prompts.v1 import (
    HEALTH_PROMPT_VERSION,
    HEALTH_SYSTEM_PROMPT_V1,
    SYMPTOM_INTAKE_SYSTEM_PROMPT_V1,
)
from app.safety.groundedness import CompositeGroundednessValidator
from app.safety.red_flags import (
    DeterministicRedFlagChecker,
    canned_response,
    canned_response_text,
)
from app.schemas.api import HealthChatRequest, HealthChatResponse
from app.schemas.corpora import HealthEntry
from app.schemas.enums import CatSex, Corner, TriageResponseKind, UrgencyTier
from app.schemas.llm import Claim, SymptomIntake, TriageResult
from app.schemas.trace import GroundednessOutcome
from app.url_safety import safe_source_url
from app.tools.contracts import (
    GroundingEvidence,
    MemoryRetriever,
    MemoryRetrieverInput,
    MemoryRetrieverOutput,
    RankedHealthEntry,
    VetKnowledgeRetriever,
    VetKnowledgeRetrieverInput,
)

logger = logging.getLogger(__name__)

class HealthOrchestrator:
    """Readable fail-closed health control flow with no hidden framework."""

    def __init__(
        self,
        *,
        llm: StructuredLLMClient,
        fast_model: str,
        health_model: str,
        vet_retriever: VetKnowledgeRetriever,
        memory_retriever: MemoryRetriever,
        memory_writer: CatMemoryService,
        red_flags: DeterministicRedFlagChecker,
        groundedness: CompositeGroundednessValidator,
        traces: TraceRepository | None = None,
    ) -> None:
        self._llm = llm
        self._traces = traces or InMemoryTraceRepository()
        self._fast_model = fast_model
        self._health_model = health_model
        self._vet = vet_retriever
        self._memory = memory_retriever
        self._memory_writer = memory_writer
        self._red_flags = red_flags
        self._groundedness = groundedness

    async def handle(
        self, request: HealthChatRequest, cat_sex: CatSex = CatSex.UNKNOWN
    ) -> HealthChatResponse:
        """Execute the strict flow and contain every operational exception."""
        collector = TraceCollector(
            cat_id=request.cat_id,
            session_id=request.session_id,
            corner=Corner.HEALTH,
            query=request.message or "",
        )
        with trace_scope(collector):
            try:
                response = await self._handle(request, cat_sex)
            except Exception:
                logger.exception("health orchestration failed closed")
                fallback = no_reliable_information()
                session_id = await record_health_safely(
                    self._memory_writer, request, fallback.message
                )
                response = HealthChatResponse(
                    session_id=session_id,
                    result=fallback,
                    generation_id=collector.generation_id,
                )
            collector.set_outcome(
                response_kind=response.result.response_kind.value,
                response_text=response.result.message,
                prompt_version=HEALTH_PROMPT_VERSION,
            )
            # Observational, and after the answer exists. `write_trace_safely`
            # cannot raise, whatever repository is installed, so no trace fault
            # can fail a user request.
            await write_trace_safely(self._traces, collector.build())
            return response

    async def _handle(
        self, request: HealthChatRequest, cat_sex: CatSex
    ) -> HealthChatResponse:
        raw_text = request.message or ""

        # Gate 1: deterministic raw keyword screen runs before any LLM call.
        raw_result = self._red_flags.check_raw(raw_text)
        if raw_result.matched_rules:
            _record_red_flag(raw_result)
            return await self._canned(request, raw_result.canned_response_id, cat_sex)

        # A caller-supplied structured intake is also checked before any LLM call.
        if request.intake is not None:
            intake = request.intake
            structured_result = self._red_flags.check_intake(intake)
            if structured_result.matched_rules:
                _record_red_flag(structured_result)
                return await self._canned(
                    request, structured_result.canned_response_id, cat_sex
                )
        else:
            extracted = await self._llm.generate(
                SymptomIntake,
                model=self._fast_model,
                purpose=ModelPurpose.FAST,
                system_prompt=SYMPTOM_INTAKE_SYSTEM_PROMPT_V1,
                cache_context="",
                user_prompt=raw_text,
                max_tokens=700,
            )
            if extracted.value is None:
                fallback = no_reliable_information()
                session_id = await record_health_safely(
                    self._memory_writer, request, fallback.message
                )
                return HealthChatResponse(
                session_id=session_id,
                result=fallback,
                generation_id=_generation_id(),
            )
            intake = extracted.value
            combined = self._red_flags.check_both(raw_text, intake)
            if combined.matched_rules:
                _record_red_flag(combined)
                return await self._canned(request, combined.canned_response_id, cat_sex)

        query = raw_text or intake.model_dump_json()
        collector = current_trace()
        retrieval_timer = (
            collector.stage_timer("retrieval") if collector else nullcontext()
        )
        with retrieval_timer:
            vet_output, memory_output, session_context = await asyncio.gather(
            self._vet.retrieve(
                VetKnowledgeRetrieverInput(
                    query=query,
                    cat_id=request.cat_id,
                )
            ),
            self._memory.retrieve(
                MemoryRetrieverInput(cat_id=request.cat_id, query=query, limit=5)
            ),
                self._memory_writer.working_context(
                    request.cat_id, request.session_id, Corner.HEALTH
                ),
            )
        record_retrieval_stages(vet_output.entries)
        record_final_context(vet_output.entries)
        record_consensus(vet_output.entries)
        if not vet_output.entries:
            fallback = no_reliable_information()
            session_id = await record_health_safely(
                self._memory_writer, request, fallback.message
            )
            return HealthChatResponse(
                session_id=session_id,
                result=fallback,
                generation_id=_generation_id(),
            )

        context = _health_context(
            vet_output.entries, memory_output, session_context
        )
        draft = await self._generate(request, intake, context)
        if draft is None:
            fallback = no_reliable_information()
            session_id = await record_health_safely(
                self._memory_writer, request, fallback.message
            )
            return HealthChatResponse(
                session_id=session_id,
                result=fallback,
                generation_id=_generation_id(),
            )

        ids = {entry.entry_id for entry in vet_output.entries}
        evidence = [
            (entry.entry_id, _health_evidence(entry.entry))
            for entry in vet_output.entries
        ]
        validation_timer = (
            collector.stage_timer("validation") if collector else nullcontext()
        )
        with validation_timer:
            verdict = await self._groundedness.validate_health(draft, ids, evidence)
        grounded = verdict.passed
        if verdict.passed:
            _set_groundedness(GroundednessOutcome.PASSED)
        if not verdict.passed:
            regenerated = await self._generate(
                request,
                intake,
                context,
                unsupported=verdict.unsupported_claims,
            )
            if regenerated is not None:
                second = await self._groundedness.validate_health(
                    regenerated, ids, evidence
                )
                if second.passed:
                    draft = regenerated
                    grounded = True
                    _set_groundedness(GroundednessOutcome.REGENERATED)
                else:
                    stripped = _strip_unsupported(
                        regenerated, second.unsupported_claims
                    )
                    if stripped is not None and _stripping_establishes_groundedness(
                        regenerated,
                        stripped,
                        second.unsupported_claims,
                        ids,
                    ):
                        draft = stripped
                        grounded = True
                        _set_groundedness(GroundednessOutcome.CLAIMS_STRIPPED)
                    else:
                        _set_groundedness(GroundednessOutcome.FAILED_FELL_BACK)
                        draft = None
            else:
                _set_groundedness(GroundednessOutcome.FAILED_FELL_BACK)
                draft = None

        if not grounded or draft is None or not draft.claims:
            fallback = no_reliable_information()
            session_id = await record_health_safely(
                self._memory_writer, request, fallback.message
            )
            return HealthChatResponse(
                session_id=session_id,
                result=fallback,
                generation_id=_generation_id(),
            )

        final = _dispose_health_draft(draft, vet_output.entries)
        session_id = await record_health_safely(
            self._memory_writer, request, final.message
        )
        return HealthChatResponse(
            session_id=session_id,
            result=final,
            generation_id=_generation_id(),
        )

    async def _generate(
        self,
        request: HealthChatRequest,
        intake: SymptomIntake,
        context: str,
        unsupported: list[str] | None = None,
    ) -> TriageResult | None:
        correction = (
            ""
            if not unsupported
            else "\nDo not repeat these unsupported claims:\n- "
            + "\n- ".join(unsupported)
        )
        result = await self._llm.generate(
            TriageResult,
            model=self._health_model,
            purpose=ModelPurpose.HEALTH,
            system_prompt=HEALTH_SYSTEM_PROMPT_V1,
            cache_context=context,
            user_prompt=(
                f"Owner message: {request.message or ''}\n"
                f"Structured intake: {intake.model_dump_json()}{correction}"
            ),
            max_tokens=1400,
        )
        return result.value

    async def _canned(
        self,
        request: HealthChatRequest,
        response_id: str | None,
        cat_sex: CatSex,
    ) -> HealthChatResponse:
        if response_id is None:
            fallback = no_reliable_information()
            session_id = await record_health_safely(
                self._memory_writer, request, fallback.message
            )
            return HealthChatResponse(
                session_id=session_id,
                result=fallback,
                generation_id=_generation_id(),
            )
        response = canned_response(response_id)
        logger.warning(
            "deterministic health safety gate fired cat_id=%s response_id=%s",
            request.cat_id,
            response_id,
        )
        result = TriageResult(
            severity=response.severity,
            claims=[],
            message=canned_response_text(response_id, cat_sex),
            retrieved_entry_ids=[],
            response_kind=TriageResponseKind.EMERGENCY_CANNED,
        )
        session_id = await record_health_safely(
            self._memory_writer,
            request,
            result.message,
            compact=False,
        )
        return HealthChatResponse(
            session_id=session_id,
            result=result,
            generation_id=_generation_id(),
        )


def _health_context(
    entries: Sequence[RankedHealthEntry],
    memory_output: MemoryRetrieverOutput,
    session_context: list[str],
) -> str:
    blocks: list[str] = []
    for ranked in entries:
        entry = ranked.entry
        blocks.append(
            f"ENTRY_ID: {entry.id}\n"
            f"TOPIC: {entry.topic}\n"
            f"SUMMARY: {entry.summary}\n"
            f"URGENCY: {entry.urgency_tier.value}\n"
            f"WHEN_TO_SEE_VET: {entry.when_to_see_vet}\n"
            f"RELATED_CONDITIONS: {' | '.join(entry.related_conditions)}\n"
            f"SOURCES: {' | '.join(_health_source_context(source.title, source.organization, source.url) for source in entry.sources)}"
        )
    result = getattr(memory_output, "result", None)
    if result is not None:
        blocks.append("ACTIVE CAT PROFILE:\n" + "\n".join(result.profile_facts))
        blocks.append(
            "ACTIVE CAT MEMORY:\n"
            + "\n".join(memory.summary for memory in result.relevant_summaries)
        )
    if session_context:
        blocks.append("CURRENT CAT/CORNER SESSION:\n" + "\n".join(session_context))
    return "\n\n".join(blocks)


def _health_evidence(entry: HealthEntry) -> str:
    return "\n".join(
        [
            entry.summary,
            entry.when_to_see_vet,
            *entry.red_flags,
            *entry.related_conditions,
        ]
    )


def _strip_unsupported(
    draft: TriageResult, unsupported_claims: list[str]
) -> TriageResult | None:
    unsupported = set(unsupported_claims)
    remaining = [claim for claim in draft.claims if claim.text not in unsupported]
    if unsupported and len(remaining) == len(draft.claims):
        return None
    if not remaining:
        return None
    return draft.model_copy(
        update={
            "claims": remaining,
            "message": " ".join(claim.text for claim in remaining),
        }
    )


def _stripping_establishes_groundedness(
    original: TriageResult,
    stripped: TriageResult,
    unsupported_claims: list[str],
    retrieved_entry_ids: set[str],
) -> bool:
    """Prove that the revised draft contains only claims the judge did not reject."""
    unsupported = set(unsupported_claims)
    original_texts = {claim.text for claim in original.claims}
    if not unsupported or not unsupported.issubset(original_texts):
        return False
    if any(claim.text in unsupported for claim in stripped.claims):
        return False
    return all(
        claim.source_entry_id in retrieved_entry_ids for claim in stripped.claims
    )


def _dispose_health_draft(
    draft: TriageResult, ranked_entries: Sequence[RankedHealthEntry]
) -> TriageResult:
    by_id = {ranked.entry_id: ranked.entry for ranked in ranked_entries}
    claims = [
        Claim(
            text=claim.text,
            source_entry_id=claim.source_entry_id,
            source_title=(
                by_id[claim.source_entry_id].sources[0].title
                if by_id[claim.source_entry_id].sources
                else None
            ),
            source_organization=(
                by_id[claim.source_entry_id].sources[0].organization
                if by_id[claim.source_entry_id].sources
                else None
            ),
            source_url=(
                safe_source_url(
                    by_id[claim.source_entry_id].sources[0].url
                )
                if by_id[claim.source_entry_id].sources
                else None
            ),
        )
        for claim in draft.claims
    ]
    message = _sanitize_health_message(draft.message, claims)
    if not message.endswith(VET_REFERRAL_LINE):
        message = f"{message}\n\n{VET_REFERRAL_LINE}"
    return TriageResult(
        severity=draft.severity,
        claims=claims,
        message=message,
        retrieved_entry_ids=[entry.entry_id for entry in ranked_entries],
        response_kind=TriageResponseKind.TRIAGE,
    )


def _health_source_context(
    title: str, organization: str, url: str | None
) -> str:
    safe_url = safe_source_url(url)
    label = f"{title} :: {organization}"
    return f"{label} :: {safe_url}" if safe_url else label


def _sanitize_health_message(message: str, claims: Sequence[Claim]) -> str:
    """Remove model-emitted citation rendering; structured claims own attribution."""
    without_section = re.sub(
        r"(?is)\s*\b(?:sources?|citations?)\s*:.*\Z", "", message
    )
    without_urls = re.sub(r"https?://\S+", "", without_section)
    without_ids = re.sub(r"\[[a-z0-9][a-z0-9-]*\]", "", without_urls)
    cleaned = " ".join(without_ids.split()).strip()
    return cleaned or " ".join(claim.text for claim in claims)


def _format_health_source(
    entry_id: str,
    title: str,
    organization: str,
    url: str | None,
) -> str:
    """Format linked and unlinked sources without inventing a dead URL."""
    label = f"[{entry_id}] {title} — {organization}"
    safe_url = safe_source_url(url)
    return f"{label}: {safe_url}" if safe_url else label


def _generation_id() -> UUID | None:
    """The in-flight generation's id, for echoing back to the client."""
    collector = current_trace()
    return None if collector is None else collector.generation_id


def _record_red_flag(result: object) -> None:
    """Record that the deterministic gate fired, and which rule matched.

    Together with `model_call_count == 0` on the trace, this is the auditable
    evidence that an emergency was answered without consulting a model.
    """
    collector = current_trace()
    if collector is None:
        return
    collector.record_red_flag(
        [str(rule) for rule in getattr(result, "matched_rules", [])],
        getattr(result, "canned_response_id", None),
    )


def _set_groundedness(outcome: GroundednessOutcome) -> None:
    collector = current_trace()
    if collector is not None:
        collector.set_groundedness(outcome)
