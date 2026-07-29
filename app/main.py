"""FastAPI application with explicit lifespan and typed error containment."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.container import build_services, close_services, set_services
from app.errors import APIErrorCode, APIErrorResponse, ApplicationError
from app.runtime_config import load_runtime_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create and close the explicit service graph."""
    settings = load_runtime_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    services = await build_services(settings)
    set_services(services)
    try:
        yield
    finally:
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
