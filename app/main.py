"""FastAPI application with explicit lifespan and typed error containment."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.rate_limit import reset_rate_limiter
from app.api.routes import router
from app.container import build_services, close_services, set_services
from app.errors import APIErrorCode, APIErrorResponse, ApplicationError
from app.logging_config import configure_logging
from app.runtime_config import load_runtime_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create and close the explicit service graph."""
    settings = load_runtime_settings()
    configure_logging(level=settings.log_level, log_format=settings.log_format)
    services = await build_services(settings)
    set_services(services)
    logging.getLogger(__name__).info(
        "application_started mode=%s behavior_model=%s health_model=%s "
        "fast_model=%s spend_window=%s cap_usd=%s",
        settings.runtime_mode.value,
        settings.anthropic_behavior_model,
        settings.anthropic_health_model,
        settings.anthropic_fast_model,
        settings.spend_window.value,
        settings.hard_spend_cap_usd,
    )
    try:
        yield
    finally:
        reset_rate_limiter()
        await close_services()


_cors_origins = load_runtime_settings().cors_allowed_origins

app = FastAPI(title="Cat Companion API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.exception_handler(ApplicationError)
async def application_error_handler(
    request: Request, exc: ApplicationError
) -> JSONResponse:
    """Render stable typed application failures."""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.error.model_dump(mode="json"),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return the typed error shape and never echo the rejected input.

    FastAPI's default 422 body reflects the offending value back to the caller.
    For these routes that value is a user's chat message, so the default would
    write user content into an error response and into any client-side log that
    captures it.
    """
    logging.getLogger(__name__).info(
        "request_validation_rejected path=%s fields=%s",
        request.url.path,
        [".".join(str(part) for part in error.get("loc", ())) for error in exc.errors()],
    )
    error = APIErrorResponse(
        code=APIErrorCode.INVALID_REQUEST,
        message="The request was not in the expected format.",
        retryable=False,
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=error.model_dump(mode="json"),
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Give framework-raised failures the same typed body as everything else."""
    codes = {
        status.HTTP_401_UNAUTHORIZED: APIErrorCode.UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN: APIErrorCode.UNAUTHORIZED,
        status.HTTP_404_NOT_FOUND: APIErrorCode.NOT_FOUND,
        status.HTTP_405_METHOD_NOT_ALLOWED: APIErrorCode.INVALID_REQUEST,
        status.HTTP_429_TOO_MANY_REQUESTS: APIErrorCode.RATE_LIMITED,
    }
    messages = {
        status.HTTP_401_UNAUTHORIZED: "Authentication is required.",
        status.HTTP_403_FORBIDDEN: "This resource is not available to this account.",
        status.HTTP_404_NOT_FOUND: "The requested resource does not exist.",
        status.HTTP_405_METHOD_NOT_ALLOWED: "That method is not allowed here.",
        status.HTTP_429_TOO_MANY_REQUESTS: "Too many requests. Try again shortly.",
    }
    error = APIErrorResponse(
        code=codes.get(exc.status_code, APIErrorCode.INTERNAL_ERROR),
        message=messages.get(
            exc.status_code, "The request could not be completed safely."
        ),
        retryable=exc.status_code >= 500 or exc.status_code == 429,
    )
    return JSONResponse(
        status_code=exc.status_code, content=error.model_dump(mode="json")
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Contain unexpected exceptions behind a typed non-sensitive response."""
    logging.getLogger(__name__).exception("unhandled request failure")
    error = APIErrorResponse(
        code=APIErrorCode.INTERNAL_ERROR,
        message="The request could not be completed safely.",
        retryable=True,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error.model_dump(mode="json"),
    )
