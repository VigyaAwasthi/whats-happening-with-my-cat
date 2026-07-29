"""Behavior and health chat route contracts."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import (
    AuthorizedCat,
    require_active_cat,
    require_matching_cat,
)
from app.container import get_services
from app.schemas.api import (
    BehaviorChatRequest,
    BehaviorChatResponse,
    HealthChatRequest,
    HealthChatResponse,
)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/behavior", response_model=BehaviorChatResponse)
async def behavior_chat(
    request: BehaviorChatRequest,
    active_cat: Annotated[AuthorizedCat, Depends(require_active_cat)],
) -> BehaviorChatResponse:
    """Return a structured behavior interpretation for one authorized cat."""
    require_matching_cat(request.cat_id, active_cat)
    return await get_services().behavior.handle(request)


@router.post("/health", response_model=HealthChatResponse)
async def health_chat(
    request: HealthChatRequest,
    active_cat: Annotated[AuthorizedCat, Depends(require_active_cat)],
) -> HealthChatResponse:
    """Return a deterministic-gated, retrieval-locked health response."""
    require_matching_cat(request.cat_id, active_cat)
    return await get_services().health.handle(request)
