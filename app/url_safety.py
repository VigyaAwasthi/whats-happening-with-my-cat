"""Shared validation and fail-safe handling for externally rendered source URLs."""

from urllib.parse import urlsplit


_PLACEHOLDER_MARKERS = (
    "[",
    "]",
    "%5bverify",
    "placeholder",
    "verify",
    "<",
    ">",
)


def safe_source_url(value: str | None) -> str | None:
    """Return a renderable absolute HTTP(S) URL, or null for unsafe input."""
    if value is None or not value:
        return None
    if value != value.strip() or any(character.isspace() for character in value):
        return None
    lowered = value.casefold()
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        return None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname:
        return None
    return value


def require_valid_source_url(
    value: str | None, *, entry_id: str, field_name: str
) -> str | None:
    """Fail ingestion loudly while still allowing a deliberately absent URL."""
    if value is None or not value:
        return None
    safe = safe_source_url(value)
    if safe is None:
        raise ValueError(
            f"entry {entry_id!r} has malformed {field_name}: {value!r}"
        )
    return safe
