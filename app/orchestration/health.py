"""Strict health flow: code gates, retrieval lock, then grounded generation."""

import asyncio
import logging
from collections.abc import Sequence

from app.llm.client import ModelPurpose, StructuredLLMClient
from app.memory.service import CatMemoryService
from app.orchestration.common import (
    VET_REFERRAL_LINE,
    no_reliable_information,
    record_health_safely,
)
from app.prompts.v1 import HEALTH_SYSTEM_PROMPT_V1, SYMPTOM_INTAKE_SYSTEM_PROMPT_V1
from app.safety.groundedness import CompositeGroundednessValidator
from app.safety.red_flags import DeterministicRedFlagChecker, canned_response
from app.schemas.api import HealthChatRequest, HealthChatResponse
from app.schemas.corpora import HealthEntry
from app.schemas.enums import Corner, TriageResponseKind, UrgencyTier
from app.schemas.llm import Claim, SymptomIntake, TriageResult
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
_URGENCY_PRIORITY = {
    UrgencyTier.ROUTINE: 0,
    UrgencyTier.MONITOR: 1,
    UrgencyTier.URGENT: 2,
    UrgencyTier.EMERGENCY: 3,
}


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
    ) -> None:
        self._llm = llm
        self._fast_model = fast_model
        self._health_model = health_model
        self._vet = vet_retriever
        self._memory = memory_retriever
        self._memory_writer = memory_writer
        self._red_flags = red_flags
        self._groundedness = groundedness

    async def handle(self, request: HealthChatRequest) -> HealthChatResponse:
        """Execute the strict flow and contain every operational exception."""
        try:
            return await self._handle(request)
        except Exception:
            logger.exception("health orchestration failed closed")
            fallback = no_reliable_information()
            session_id = await record_health_safely(
                self._memory_writer, request, fallback.message
            )
            return HealthChatResponse(session_id=session_id, result=fallback)

    async def _handle(self, request: HealthChatRequest) -> HealthChatResponse:
        raw_text = request.message or ""

        # Gate 1: deterministic raw keyword screen runs before any LLM call.
        raw_result = self._red_flags.check_raw(raw_text)
        if raw_result.matched_rules:
            return await self._canned(request, raw_result.canned_response_id)

        # A caller-supplied structured intake is also checked before any LLM call.
        if request.intake is not None:
            intake = request.intake
            structured_result = self._red_flags.check_intake(intake)
            if structured_result.matched_rules:
                return await self._canned(
                    request, structured_result.canned_response_id
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
                return HealthChatResponse(session_id=session_id, result=fallback)
            intake = extracted.value
            combined = self._red_flags.check_both(raw_text, intake)
            if combined.matched_rules:
                return await self._canned(request, combined.canned_response_id)

        query = raw_text or intake.model_dump_json()
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
        if not vet_output.entries:
            fallback = no_reliable_information()
            session_id = await record_health_safely(
                self._memory_writer, request, fallback.message
            )
            return HealthChatResponse(session_id=session_id, result=fallback)

        context = _health_context(
            vet_output.entries, memory_output, session_context
        )
        draft = await self._generate(request, intake, context)
        if draft is None:
            fallback = no_reliable_information()
            session_id = await record_health_safely(
                self._memory_writer, request, fallback.message
            )
            return HealthChatResponse(session_id=session_id, result=fallback)

        ids = {entry.entry_id for entry in vet_output.entries}
        evidence = [
            (entry.entry_id, _health_evidence(entry.entry))
            for entry in vet_output.entries
        ]
        verdict = await self._groundedness.validate_health(draft, ids, evidence)
        grounded = verdict.passed
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
                    else:
                        draft = None
            else:
                draft = None

        if not grounded or draft is None or not draft.claims:
            fallback = no_reliable_information()
            session_id = await record_health_safely(
                self._memory_writer, request, fallback.message
            )
            return HealthChatResponse(session_id=session_id, result=fallback)

        final = _dispose_health_draft(draft, vet_output.entries)
        session_id = await record_health_safely(
            self._memory_writer, request, final.message
        )
        return HealthChatResponse(session_id=session_id, result=final)

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
        self, request: HealthChatRequest, response_id: str | None
    ) -> HealthChatResponse:
        if response_id is None:
            fallback = no_reliable_information()
            session_id = await record_health_safely(
                self._memory_writer, request, fallback.message
            )
            return HealthChatResponse(session_id=session_id, result=fallback)
        response = canned_response(response_id)
        logger.warning(
            "deterministic health safety gate fired cat_id=%s response_id=%s",
            request.cat_id,
            response_id,
        )
        result = TriageResult(
            severity=response.severity,
            claims=[],
            message=response.text,
            retrieved_entry_ids=[],
            response_kind=TriageResponseKind.EMERGENCY_CANNED,
        )
        session_id = await record_health_safely(
            self._memory_writer, request, result.message
        )
        return HealthChatResponse(session_id=session_id, result=result)


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
            f"SOURCES: {' | '.join(source.url for source in entry.sources)}"
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
            source_url=(
                by_id[claim.source_entry_id].sources[0].url
                if by_id[claim.source_entry_id].sources
                else None
            ),
        )
        for claim in draft.claims
    ]
    cited_ids = {claim.source_entry_id for claim in claims}
    cited_entries = [
        ranked for ranked in ranked_entries if ranked.entry_id in cited_ids
    ]
    severities = [draft.severity] + [
        ranked.entry.urgency_tier for ranked in cited_entries
    ]
    severity = max(severities, key=_URGENCY_PRIORITY.__getitem__)
    citations = "\n".join(
        f"[{entry.entry_id}] {source.url}"
        for entry in cited_entries
        for source in entry.entry.sources
    )
    message = draft.message.rstrip()
    if citations:
        message = f"{message}\n\nSources:\n{citations}"
    if not message.endswith(VET_REFERRAL_LINE):
        message = f"{message}\n\n{VET_REFERRAL_LINE}"
    return TriageResult(
        severity=severity,
        claims=claims,
        message=message,
        retrieved_entry_ids=[entry.entry_id for entry in ranked_entries],
        response_kind=TriageResponseKind.TRIAGE,
    )
