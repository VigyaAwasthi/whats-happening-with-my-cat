"""Feedback route contract."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.dependencies import require_current_account
from app.container import get_services
from app.errors import APIErrorCode, APIErrorResponse, ApplicationError
from app.schemas.api import FeedbackRequest, FeedbackResponse

router = APIRouter(tags=["feedback"])


@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_feedback(
    request: FeedbackRequest,
    account_id: Annotated[UUID, Depends(require_current_account)],
) -> FeedbackResponse:
    """Persist thumbs and optional helpfulness for one cat-scoped session."""
    if not await get_services().repository.owns_cat(account_id, request.cat_id):
        raise ApplicationError(
            403,
            APIErrorResponse(
                code=APIErrorCode.UNAUTHORIZED,
                message="The feedback cat does not belong to this account.",
                retryable=False,
            ),
        )
    return FeedbackResponse(
        feedback=await get_services().repository.write_feedback(request)
    )
