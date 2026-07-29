"""AI-inaccessible scrapbook route contracts."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import (
    AuthorizedCat,
    require_active_cat,
    require_matching_cat,
)
from app.container import get_services
from app.errors import APIErrorCode, APIErrorResponse, ApplicationError
from app.schemas.api import (
    DeleteResponse,
    MomentCreateRequest,
    MomentDeleteRequest,
    MomentListRequest,
    MomentListResponse,
    MomentResponse,
)

router = APIRouter(prefix="/moments", tags=["moments"])


@router.get("", response_model=MomentListResponse)
async def list_moments(
    request: Annotated[MomentListRequest, Query()],
    active_cat: Annotated[AuthorizedCat, Depends(require_active_cat)],
) -> MomentListResponse:
    """List scrapbook data for one cat without exposing it to AI retrieval."""
    require_matching_cat(request.cat_id, active_cat)
    return MomentListResponse(
        cat_id=request.cat_id,
        moments=await get_services().repository.list_moments(request.cat_id),
    )


@router.post("", response_model=MomentResponse, status_code=status.HTTP_201_CREATED)
async def create_moment(
    request: MomentCreateRequest,
    active_cat: Annotated[AuthorizedCat, Depends(require_active_cat)],
) -> MomentResponse:
    """Create a scrapbook item scoped to one authorized cat."""
    require_matching_cat(request.cat_id, active_cat)
    return MomentResponse(
        moment=await get_services().repository.create_moment(request)
    )


@router.delete("", response_model=DeleteResponse)
async def delete_moment(
    request: Annotated[MomentDeleteRequest, Query()],
    active_cat: Annotated[AuthorizedCat, Depends(require_active_cat)],
) -> DeleteResponse:
    """Delete one scrapbook item within its authorized cat scope."""
    require_matching_cat(request.cat_id, active_cat)
    deleted = await get_services().repository.delete_moment(
        request.cat_id, request.moment_id
    )
    if not deleted:
        raise ApplicationError(
            404,
            APIErrorResponse(
                code=APIErrorCode.NOT_FOUND,
                message="Moment not found for the active cat.",
                retryable=False,
            ),
        )
    return DeleteResponse(deleted_id=request.moment_id, deleted=True)
