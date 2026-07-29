"""Curated fun-fact route contracts."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import (
    AuthorizedCat,
    require_active_cat,
    require_matching_cat,
)
from app.container import get_services
from app.errors import APIErrorCode, APIErrorResponse, ApplicationError
from app.schemas.api import (
    FunFactDetailRequest,
    FunFactDetailResponse,
    FunFactListRequest,
    FunFactListResponse,
)

router = APIRouter(prefix="/facts", tags=["facts"])


@router.get("", response_model=FunFactListResponse)
async def list_facts(
    request: Annotated[FunFactListRequest, Query()],
    active_cat: Annotated[AuthorizedCat, Depends(require_active_cat)],
) -> FunFactListResponse:
    """Return curated cards selected from explicit active-cat tags."""
    require_matching_cat(request.cat_id, active_cat)
    facts = await get_services().repository.list_facts(
        request.cat_id, request.tags, request.exclude_ids
    )
    return FunFactListResponse(facts=facts)


@router.get("/{id}", response_model=FunFactDetailResponse)
async def get_fact(
    id: str,
    request: Annotated[FunFactDetailRequest, Query()],
    active_cat: Annotated[AuthorizedCat, Depends(require_active_cat)],
) -> FunFactDetailResponse:
    """Return one curated fact and its pre-written detail expansion."""
    require_matching_cat(request.cat_id, active_cat)
    fact = await get_services().repository.get_fact(request.cat_id, id)
    if fact is None:
        raise ApplicationError(
            404,
            APIErrorResponse(
                code=APIErrorCode.NOT_FOUND,
                message="Fun fact not found.",
                retryable=False,
            ),
        )
    return fact
