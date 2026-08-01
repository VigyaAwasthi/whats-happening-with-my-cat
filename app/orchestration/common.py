"""Shared safe fallbacks and memory-write containment."""

import logging
from uuid import UUID

from app.memory.service import CatMemoryService
from app.schemas.api import HealthChatRequest
from app.schemas.enums import Corner, TriageResponseKind, UrgencyTier
from app.schemas.llm import TriageResult

logger = logging.getLogger(__name__)

VET_REFERRAL_LINE = "Please contact a veterinarian for guidance about your cat."


def no_reliable_information() -> TriageResult:
    """Retrieval-locked refusal used whenever support is absent or validation fails."""
    return TriageResult(
        severity=UrgencyTier.ROUTINE,
        claims=[],
        message=(
            "I could not find reliable information in the trusted veterinary sources "
            f"for this question. {VET_REFERRAL_LINE}"
        ),
        retrieved_entry_ids=[],
        response_kind=TriageResponseKind.NO_RELIABLE_INFORMATION,
    )


async def record_health_safely(
    memory: CatMemoryService,
    request: HealthChatRequest,
    assistant_message: str,
    *,
    compact: bool = True,
) -> UUID:
    """Memory failure never changes or leaks a health response."""
    try:
        user_message = request.message
        if not user_message and request.intake is not None:
            user_message = request.intake.model_dump_json()
        if not user_message:
            return request.session_id
        return await memory.record_exchange(
            cat_id=request.cat_id,
            requested_session_id=request.session_id,
            corner=Corner.HEALTH,
            user_message=user_message,
            assistant_message=assistant_message,
            compact=compact,
        )
    except Exception:
        logger.exception("health memory write failed without affecting response")
        return request.session_id
