"""Feedback route contract.

Feedback points at a `generation_id`, not just a session, so a rating joins to
the trace that explains the answer. It is editable (re-submitting for the same
generation replaces the rating) and revocable (DELETE removes it).
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import require_current_account
from app.container import get_services
from app.errors import APIErrorCode, APIErrorResponse, ApplicationError
from app.schemas.api import (
    DeleteResponse,
    FeedbackDeleteRequest,
    FeedbackRequest,
    FeedbackResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["feedback"])


async def _require_owned_cat(account_id: UUID, cat_id: UUID) -> None:
    if not await get_services().repository.owns_cat(account_id, cat_id):
        raise ApplicationError(
            status.HTTP_403_FORBIDDEN,
            APIErrorResponse(
                code=APIErrorCode.UNAUTHORIZED,
                message="The feedback cat does not belong to this account.",
                retryable=False,
            ),
        )


@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_feedback(
    request: FeedbackRequest,
    account_id: Annotated[UUID, Depends(require_current_account)],
) -> FeedbackResponse:
    """Record or replace the rating for one generated answer."""
    services = get_services()
    await _require_owned_cat(account_id, request.cat_id)

    write_request = request
    if request.generation_id is not None and services.traces is not None:
        # A rating must not be able to point at another cat's generation: the
        # trace is the join key into user content, so its scope is enforced here
        # rather than trusted from the client.
        trace = await services.traces.get(request.generation_id)
        if trace is not None and trace.cat_id != request.cat_id:
            raise ApplicationError(
                status.HTTP_403_FORBIDDEN,
                APIErrorResponse(
                    code=APIErrorCode.CAT_SCOPE_MISMATCH,
                    message="That response belongs to a different cat.",
                    retryable=False,
                ),
            )
        if trace is None:
            # The trace write may have failed, or retention may have pruned it.
            # Preserve the rating as explicitly untraceable instead of sending
            # an unknown foreign key to PostgreSQL.
            logger.info(
                "feedback references an unknown generation generation_id=%s",
                request.generation_id,
            )
            write_request = request.model_copy(update={"generation_id": None})

    return FeedbackResponse(
        feedback=await services.repository.write_feedback(write_request)
    )


@router.delete("/feedback", response_model=DeleteResponse)
async def revoke_feedback(
    request: Annotated[FeedbackDeleteRequest, Query()],
    account_id: Annotated[UUID, Depends(require_current_account)],
) -> DeleteResponse:
    """Withdraw a rating. Users may change their mind about telling us."""
    await _require_owned_cat(account_id, request.cat_id)
    revoked = await get_services().repository.revoke_feedback(
        request.cat_id, request.feedback_id
    )
    if not revoked:
        raise ApplicationError(
            status.HTTP_404_NOT_FOUND,
            APIErrorResponse(
                code=APIErrorCode.NOT_FOUND,
                message="That feedback no longer exists.",
                retryable=False,
            ),
        )
    return DeleteResponse(deleted_id=request.feedback_id, deleted=True)
