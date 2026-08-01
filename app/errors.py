"""Typed application failures converted to stable API responses."""

from enum import Enum

from pydantic import Field

from app.schemas.base import ContractModel


class APIErrorCode(str, Enum):
    """Closed API failure codes."""

    NO_ACTIVE_CAT = "NO_ACTIVE_CAT"
    CAT_SCOPE_MISMATCH = "CAT_SCOPE_MISMATCH"
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    CAT_LIMIT_REACHED = "CAT_LIMIT_REACHED"
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    EMAIL_NOT_CONFIRMED = "EMAIL_NOT_CONFIRMED"
    EMAIL_ALREADY_REGISTERED = "EMAIL_ALREADY_REGISTERED"
    RATE_LIMITED = "RATE_LIMITED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class APIErrorResponse(ContractModel):
    """Stable typed error body returned by the FastAPI exception handler."""

    code: APIErrorCode = Field(description="Machine-actionable API error code.")
    message: str = Field(min_length=1, description="Safe user-facing error message.")
    retryable: bool = Field(description="Whether retrying may succeed.")


class ApplicationError(Exception):
    """Internal control-flow exception carrying only a typed safe response."""

    def __init__(self, status_code: int, error: APIErrorResponse) -> None:
        super().__init__(error.message)
        self.status_code = status_code
        self.error = error

