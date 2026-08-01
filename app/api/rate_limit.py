"""Per-account request limiting on the two model-calling chat endpoints.

This is cost protection, not a security control. Without it a single account
can drain the application-wide spend cap and take the health and behavior
corners offline for everyone else. It is deliberately simple: a fixed window
counted in process memory, no Redis, no dependency.

Two consequences of that simplicity, both acceptable here and both documented
in DEPLOYMENT.md:

* The limit is per process. With N replicas the effective ceiling is N times
  the configured value. The hard spend cap in `SpendTracker` is the real
  backstop and *is* shared across processes; this only stops one account
  getting there first.
* Counters reset on deploy. An attacker-grade control would not accept that;
  a cost guard does.
"""

import time
from collections import defaultdict, deque
from typing import Annotated
from uuid import UUID

from fastapi import Depends, status

from app.api.dependencies import AuthorizedCat, require_active_cat
from app.container import get_services
from app.errors import APIErrorCode, APIErrorResponse, ApplicationError

_WINDOW_SECONDS = 60.0


class AccountRateLimiter:
    """Fixed-window request counter keyed by account."""

    def __init__(self, *, limit_per_minute: int) -> None:
        self._limit = limit_per_minute
        self._hits: dict[UUID, deque[float]] = defaultdict(deque)

    def check(self, account_id: UUID, *, now: float | None = None) -> float | None:
        """Record a request; return seconds to wait if the account is over.

        Returning ``None`` means the request is allowed and has been counted.
        """
        moment = now if now is not None else time.monotonic()
        self._evict_idle(moment)
        hits = self._hits[account_id]
        cutoff = moment - _WINDOW_SECONDS
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self._limit:
            return max(0.0, hits[0] + _WINDOW_SECONDS - moment)
        hits.append(moment)
        return None

    def _evict_idle(self, moment: float) -> None:
        """Drop accounts with no recent activity so the map cannot grow forever."""
        if len(self._hits) < 1024:
            return
        cutoff = moment - _WINDOW_SECONDS
        for account_id in [
            key
            for key, hits in self._hits.items()
            if not hits or hits[-1] <= cutoff
        ]:
            del self._hits[account_id]


_limiter: AccountRateLimiter | None = None


def get_rate_limiter() -> AccountRateLimiter:
    """Build the limiter lazily from settings, then reuse it for the process."""
    global _limiter
    if _limiter is None:
        _limiter = AccountRateLimiter(
            limit_per_minute=get_services().settings.chat_rate_limit_per_minute
        )
    return _limiter


def reset_rate_limiter() -> None:
    """Drop the process-wide limiter; used by tests and by lifespan teardown."""
    global _limiter
    _limiter = None


async def require_chat_quota(
    active_cat: Annotated[AuthorizedCat, Depends(require_active_cat)],
) -> AuthorizedCat:
    """Reject a chat request that would exceed the account's per-minute budget."""
    retry_after = get_rate_limiter().check(active_cat.account_id)
    if retry_after is not None:
        raise ApplicationError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            APIErrorResponse(
                code=APIErrorCode.RATE_LIMITED,
                message=(
                    "Too many messages in a short period. "
                    "Wait about a minute and try again."
                ),
                retryable=True,
            ),
        )
    return active_cat
