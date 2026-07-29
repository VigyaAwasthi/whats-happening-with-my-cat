"""Behavior flow with medical handoff and code-controlled clarifying questions."""

import asyncio
import logging
from collections.abc import Sequence
from uuid import UUID

from app.llm.client import ModelPurpose, StructuredLLMClient
from app.memory.service import CatMemoryService
from app.prompts.v1 import BEHAVIOR_SYSTEM_PROMPT_V1, HEALTH_SIGNAL_SYSTEM_PROMPT_V1
from app.safety.groundedness import CompositeGroundednessValidator
from app.safety.red_flags import DeterministicRedFlagChecker
from app.schemas.api import BehaviorChatRequest, BehaviorChatResponse
from app.schemas.corpora import BehaviorEntry
from app.schemas.enums import ConfidenceLevel, Corner
from app.schemas.llm import BehaviorInterpretation, HealthSignalCheck
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
        behavior_retriever: BehaviorKnowledgeRetriever,
        memory_retriever: MemoryRetriever,
        memory_writer: CatMemoryService,
        red_flags: DeterministicRedFlagChecker,
        groundedness: CompositeGroundednessValidator,
    ) -> None:
        self._llm = llm
        self._fast_model = fast_model
        self._behavior_model = behavior_model
        self._threshold = health_signal_threshold
        self._behavior = behavior_retriever
        self._memory = memory_retriever
        self._memory_writer = memory_writer
        self._red_flags = red_flags
        self._groundedness = groundedness

    async def handle(self, request: BehaviorChatRequest) -> BehaviorChatResponse:
        try:
            return await self._handle(request)
        except Exception:
            logger.exception("behavior orchestration failed closed")
            return BehaviorChatResponse(
                session_id=request.session_id, result=_behavior_fallback()
            )

    async def _handle(self, request: BehaviorChatRequest) -> BehaviorChatResponse:
        if self._red_flags.check_raw(request.message).matched_rules:
            return await self._nudge(request)

        signal = await self._llm.generate(
            HealthSignalCheck,
            model=self._fast_model,
            purpose=ModelPurpose.FAST,
            system_prompt=HEALTH_SIGNAL_SYSTEM_PROMPT_V1,
            cache_context="",
            user_prompt=request.message,
            max_tokens=300,
        )
        if signal.value is None:
            return await self._nudge(request)
        if (
            signal.value.has_medical_signal
            and signal.value.confidence >= self._threshold
        ):
            return await self._nudge(request)

        behavior_output, memory_output, session_context = await asyncio.gather(
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
        if not behavior_output.entries:
            result = _behavior_fallback()
            session_id = await self._record(request, result)
            return BehaviorChatResponse(session_id=session_id, result=result)

        context = _behavior_context(
            behavior_output.entries, memory_output, session_context
        )
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
            result = _source_fallback(behavior_output.entries[0].entry)
            session_id = await self._record(request, result)
            return BehaviorChatResponse(session_id=session_id, result=result)

        result = generated.value
        if result.medical_nudge:
            return await self._nudge(request)
        ids = {entry.entry_id for entry in behavior_output.entries}
        if any(entry_id not in ids for entry_id in result.cited_entry_ids):
            result = _source_fallback(behavior_output.entries[0].entry)
        else:
            allowed_questions = {
                question
                for ranked in behavior_output.entries
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
            verdict = await self._groundedness.validate(
                GroundednessValidatorInput(
                    draft_answer=f"{result.interpretation}\n{result.reasoning}",
                    retrieved_entries=[
                        GroundingEvidence(
                            entry_id=ranked.entry_id, text=ranked.entry.summary
                        )
                        for ranked in behavior_output.entries
                    ],
                )
            )
            if verdict.verdict is None or not verdict.verdict.passed:
                result = _source_fallback(behavior_output.entries[0].entry)

        session_id = await self._record(request, result)
        return BehaviorChatResponse(session_id=session_id, result=result)

    async def _nudge(
        self, request: BehaviorChatRequest
    ) -> BehaviorChatResponse:
        result = BehaviorInterpretation(
            interpretation=(
                "That could be medical rather than behavioral. Please use the health "
                "corner and contact a veterinarian if the signs are severe or worsening."
            ),
            confidence=ConfidenceLevel.GENERAL,
            reasoning="A medical signal was detected, so behavior interpretation stopped.",
            cited_entry_ids=[],
            suggested_clarifying_questions=[],
            medical_nudge=True,
        )
        session_id = await self._record(request, result)
        return BehaviorChatResponse(session_id=session_id, result=result)

    async def _record(
        self, request: BehaviorChatRequest, result: BehaviorInterpretation
    ) -> UUID:
        try:
            return await self._memory_writer.record_exchange(
                cat_id=request.cat_id,
                requested_session_id=request.session_id,
                corner=Corner.BEHAVIOR,
                user_message=request.message,
                assistant_message=result.interpretation,
            )
        except Exception:
            logger.exception("behavior memory write failed without affecting response")
            return request.session_id


def _behavior_context(
    entries: Sequence[RankedBehaviorEntry],
    memory_output: MemoryRetrieverOutput,
    session_context: list[str],
) -> str:
    blocks = [
        (
            f"ENTRY_ID: {ranked.entry_id}\n"
            f"SUMMARY: {ranked.entry.summary}\n"
            f"CONFIDENCE: {ranked.entry.confidence.value}\n"
            f"CLARIFYING_QUESTIONS: {' | '.join(ranked.entry.clarifying_questions)}"
        )
        for ranked in entries
    ]
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
        confidence=entry.confidence,
        reasoning="This wording is limited to the retrieved behavior source.",
        cited_entry_ids=[entry.id],
        suggested_clarifying_questions=entry.clarifying_questions[:2],
        medical_nudge=False,
    )


def _behavior_fallback() -> BehaviorInterpretation:
    return BehaviorInterpretation(
        interpretation=(
            "I do not have enough reliable context to interpret that behavior yet."
        ),
        confidence=ConfidenceLevel.VARIES_BY_CAT,
        reasoning="No validated behavior context was available.",
        cited_entry_ids=[],
        suggested_clarifying_questions=[],
        medical_nudge=False,
    )
