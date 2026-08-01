"""Behavior and health chat route contracts."""

import logging

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import (
    AuthorizedCat,
    require_matching_cat,
)
from app.api.rate_limit import require_chat_quota
from app.container import get_services
from app.schemas.enums import CatSex
from app.schemas.api import (
    BehaviorChatRequest,
    BehaviorChatResponse,
    HealthChatRequest,
    HealthChatResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/behavior", response_model=BehaviorChatResponse)
async def behavior_chat(
    request: BehaviorChatRequest,
    active_cat: Annotated[AuthorizedCat, Depends(require_chat_quota)],
) -> BehaviorChatResponse:
    """Return a structured behavior interpretation for one authorized cat."""
    require_matching_cat(request.cat_id, active_cat)
    return await get_services().behavior.handle(request)


@router.post("/health", response_model=HealthChatResponse)
async def health_chat(
    request: HealthChatRequest,
    active_cat: Annotated[AuthorizedCat, Depends(require_chat_quota)],
) -> HealthChatResponse:
    """Return a deterministic-gated, retrieval-locked health response."""
    require_matching_cat(request.cat_id, active_cat)
    services = get_services()
    cat_sex = CatSex.UNKNOWN
    try:
        cats = await services.repository.list_cats(active_cat.account_id)
        cat_sex = next(
            (cat.sex for cat in cats if cat.id == active_cat.cat_id),
            CatSex.UNKNOWN,
        )
    except Exception:
        # Failure to load an optional refinement must never suppress the urgent base text.
        logger.warning(
            "cat sex lookup failed; using conservative unknown framing cat_id=%s",
            active_cat.cat_id,
        )
    return await services.health.handle(request, cat_sex=cat_sex)
