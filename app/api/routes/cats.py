"""Cat profile CRUD route contracts."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from psycopg.errors import CheckViolation

from app.api.dependencies import require_current_account
from app.container import get_services
from app.errors import APIErrorCode, APIErrorResponse, ApplicationError
from app.schemas.api import (
    CatCreateRequest,
    CatDeleteRequest,
    CatListResponse,
    CatPatchRequest,
    CatResponse,
    DeleteResponse,
)

router = APIRouter(prefix="/cats", tags=["cats"])


@router.get("", response_model=CatListResponse)
async def list_cats(
    account_id: Annotated[UUID, Depends(require_current_account)],
) -> CatListResponse:
    """List an account roster; zero cats is a valid onboarding state."""
    return CatListResponse(cats=await get_services().repository.list_cats(account_id))


@router.post("", response_model=CatResponse, status_code=status.HTTP_201_CREATED)
async def create_cat(
    request: CatCreateRequest,
    account_id: Annotated[UUID, Depends(require_current_account)],
) -> CatResponse:
    """Create a cat; the persistence boundary enforces the hard ten-cat cap."""
    try:
        profile = await get_services().repository.create_cat(account_id, request)
    except CheckViolation as exc:
        raise ApplicationError(
            status.HTTP_409_CONFLICT,
            APIErrorResponse(
                code=APIErrorCode.CAT_LIMIT_REACHED,
                message="An account may have at most 10 cats.",
                retryable=False,
            ),
        ) from exc
    if profile is None:
        raise ApplicationError(
            status.HTTP_409_CONFLICT,
            APIErrorResponse(
                code=APIErrorCode.CAT_LIMIT_REACHED,
                message="The cat could not be created; the account may be at its limit.",
                retryable=False,
            ),
        )
    memory_repository = get_services().memory_repository
    if hasattr(memory_repository, "profiles"):
        memory_repository.profiles[profile.id] = profile
    return CatResponse(cat=profile)


@router.patch("", response_model=CatResponse)
async def patch_cat(
    request: CatPatchRequest,
    account_id: Annotated[UUID, Depends(require_current_account)],
) -> CatResponse:
    """Patch only the explicitly scoped and authorized cat."""
    profile = await get_services().repository.patch_cat(account_id, request)
    if profile is None:
        raise _not_found()
    memory_repository = get_services().memory_repository
    if hasattr(memory_repository, "profiles"):
        memory_repository.profiles[profile.id] = profile
    return CatResponse(cat=profile)


@router.delete("", response_model=DeleteResponse)
async def delete_cat(
    request: Annotated[CatDeleteRequest, Query()],
    account_id: Annotated[UUID, Depends(require_current_account)],
) -> DeleteResponse:
    """Delete only the explicitly scoped cat and its cascading owned data."""
    deleted = await get_services().repository.delete_cat(account_id, request.cat_id)
    if not deleted:
        raise _not_found()
    memory_repository = get_services().memory_repository
    if hasattr(memory_repository, "profiles"):
        memory_repository.profiles.pop(request.cat_id, None)
    return DeleteResponse(deleted_id=request.cat_id, deleted=True)


def _not_found() -> ApplicationError:
    return ApplicationError(
        status.HTTP_404_NOT_FOUND,
        APIErrorResponse(
            code=APIErrorCode.NOT_FOUND,
            message="Cat not found for this account.",
            retryable=False,
        ),
    )
