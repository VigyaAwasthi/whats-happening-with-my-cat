"""Authentication and active-cat authorization dependencies."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, status
from pydantic import Field

from app.container import get_services
from app.errors import APIErrorCode, APIErrorResponse, ApplicationError
from app.schemas.base import ContractModel


class AuthorizedCat(ContractModel):
    """Authenticated account-and-cat scope supplied to every cat-scoped handler."""

    account_id: UUID = Field(description="Authenticated owning account identifier.")
    cat_id: UUID = Field(description="Authorized active-cat isolation key.")


async def require_current_account(
    authorization: Annotated[str | None, Header()] = None,
) -> UUID:
    """Resolve the authenticated Supabase subject to an internal account."""
    token = None
    if authorization:
        scheme, separator, credential = authorization.partition(" ")
        if separator and scheme.casefold() == "bearer":
            token = credential
    account_id = await get_services().auth.resolve_account(token)
    if account_id is None:
        raise ApplicationError(
            status.HTTP_401_UNAUTHORIZED,
            APIErrorResponse(
                code=APIErrorCode.UNAUTHORIZED,
                message="Authentication is required.",
                retryable=False,
            ),
        )
    return account_id


async def require_active_cat(
    account_id: Annotated[UUID, Depends(require_current_account)],
    active_cat_id: Annotated[
        UUID | None,
        Header(
            alias="X-Active-Cat-ID",
            description="Required active-cat identifier for structural request scoping.",
        ),
    ] = None,
) -> AuthorizedCat:
    """Resolve and authorize the active cat for the current account.

    No cat-scoped handler may bypass this dependency. Route request models also carry
    ``cat_id``; the implementation must reject any mismatch with this authoritative
    header before reading or writing cat data.
    """
    if active_cat_id is None:
        raise ApplicationError(
            status.HTTP_409_CONFLICT,
            APIErrorResponse(
                code=APIErrorCode.NO_ACTIVE_CAT,
                message="Create or select a cat before using this corner.",
                retryable=False,
            ),
        )
    if not await get_services().repository.owns_cat(account_id, active_cat_id):
        cats = await get_services().repository.list_cats(account_id)
        code = APIErrorCode.NO_ACTIVE_CAT if not cats else APIErrorCode.UNAUTHORIZED
        message = (
            "Create a cat before using this corner."
            if not cats
            else "The selected cat does not belong to this account."
        )
        raise ApplicationError(
            status.HTTP_409_CONFLICT if not cats else status.HTTP_403_FORBIDDEN,
            APIErrorResponse(code=code, message=message, retryable=False),
        )
    return AuthorizedCat(account_id=account_id, cat_id=active_cat_id)


def require_matching_cat(request_cat_id: UUID, active_cat: AuthorizedCat) -> None:
    """Reject disagreement between body/query scope and authorized active header."""
    if request_cat_id != active_cat.cat_id:
        raise ApplicationError(
            status.HTTP_409_CONFLICT,
            APIErrorResponse(
                code=APIErrorCode.CAT_SCOPE_MISMATCH,
                message="The request cat_id does not match the active cat.",
                retryable=False,
            ),
        )
