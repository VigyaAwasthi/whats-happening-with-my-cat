"""Unauthenticated liveness and readiness probes used by the platform.

Neither endpoint requires authentication and neither reveals configuration.
`/ready` reports coarse per-subsystem booleans only: enough for an operator to
tell a database outage from a configuration fault, and not enough for an
anonymous caller to learn a hostname, a model id, or which setting is missing.
The detail goes to the logs.
"""

import logging

from fastapi import APIRouter, Response, status

from app.container import get_services
from app.schemas.base import ContractModel
from pydantic import Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


class HealthResponse(ContractModel):
    """Liveness: the process is running and can serve a request."""

    status: str = Field(description="Always 'ok' when the process is alive.")


class ReadinessChecks(ContractModel):
    """Coarse subsystem booleans; deliberately carries no configuration detail."""

    database: bool = Field(description="A trivial query against the database succeeded.")
    configuration: bool = Field(description="Required runtime settings resolved.")


class ReadinessResponse(ContractModel):
    """Readiness: this instance can serve real traffic right now."""

    status: str = Field(description="'ready' or 'not_ready'.")
    checks: ReadinessChecks = Field(description="Per-subsystem outcome.")


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Report process liveness without touching any dependency.

    Deliberately does not check the database. A liveness probe that fails on a
    database blip makes the platform restart healthy processes during an
    outage, turning a recoverable dependency failure into a restart loop.
    """
    return HealthResponse(status="ok")


@router.get("/ready", response_model=ReadinessResponse)
async def ready(response: Response) -> ReadinessResponse:
    """Report whether dependencies are reachable and configuration resolved."""
    configuration_ok = False
    database_ok = False
    try:
        services = get_services()
        configuration_ok = True
    except RuntimeError:
        logger.warning("readiness: application services are not initialized")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status="not_ready",
            checks=ReadinessChecks(database=False, configuration=False),
        )

    if services.database is None:
        # Development mode has no database by design and is ready without one.
        database_ok = True
    else:
        try:
            await services.database.fetch_one("SELECT 1 AS ok")
            database_ok = True
        except Exception:
            logger.exception("readiness: database probe failed")

    ready_now = configuration_ok and database_ok
    if not ready_now:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if ready_now else "not_ready",
        checks=ReadinessChecks(
            database=database_ok, configuration=configuration_ok
        ),
    )
