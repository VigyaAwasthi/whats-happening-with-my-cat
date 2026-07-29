"""Supabase Auth wrapper route contracts."""

from fastapi import APIRouter, status

from app.container import get_services
from app.schemas.api import AuthSessionRequest, AuthSessionResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/sign-up",
    response_model=AuthSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def sign_up(request: AuthSessionRequest) -> AuthSessionResponse:
    """Create a Supabase Auth identity, internal account, and session."""
    return await get_services().auth.sign_up(request)


@router.post(
    "/sign-in",
    response_model=AuthSessionResponse,
    status_code=status.HTTP_200_OK,
)
async def sign_in(request: AuthSessionRequest) -> AuthSessionResponse:
    """Create a Supabase Auth session."""
    return await get_services().auth.sign_in(request)
