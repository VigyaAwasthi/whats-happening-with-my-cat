"""Account portability and deletion route contracts."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.dependencies import require_current_account
from app.container import get_services
from app.errors import APIErrorCode, APIErrorResponse, ApplicationError
from app.schemas.api import AccountDeleteResponse, AccountExportResponse

router = APIRouter(prefix="/account", tags=["account"])


@router.get("/export", response_model=AccountExportResponse)
async def export_account(
    account_id: Annotated[UUID, Depends(require_current_account)],
) -> AccountExportResponse:
    """Export all authenticated-account data with cat scopes preserved."""
    result = await get_services().repository.export_account(account_id)
    if result is None:
        raise ApplicationError(
            404,
            APIErrorResponse(
                code=APIErrorCode.NOT_FOUND,
                message="Account not found.",
                retryable=False,
            ),
        )
    return result


@router.delete("", response_model=AccountDeleteResponse)
async def delete_account(
    account_id: Annotated[UUID, Depends(require_current_account)],
) -> AccountDeleteResponse:
    """Delete the authenticated account and its complete database cascade."""
    services = get_services()
    auth_subject_id = await services.repository.get_auth_subject(account_id)
    if auth_subject_id is None:
        raise ApplicationError(
            status.HTTP_404_NOT_FOUND,
            APIErrorResponse(
                code=APIErrorCode.NOT_FOUND,
                message="Account not found.",
                retryable=False,
            ),
        )
    if not await services.auth.delete_identity(auth_subject_id):
        raise ApplicationError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            APIErrorResponse(
                code=APIErrorCode.SERVICE_UNAVAILABLE,
                message="The authentication identity could not be deleted safely.",
                retryable=True,
            ),
        )
    deleted = await services.repository.delete_account(account_id)
    return AccountDeleteResponse(account_id=account_id, deleted=deleted)
