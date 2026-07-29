"""Resolve the curated corpus location without hiding missing source data."""

from pathlib import Path


PROMPT_CORPUS_DIR = Path(
    "/Users/vigyaawasthi/Documents/Whats happening with my cat/"
)
WORKSPACE_CORPUS_DIR = Path(
    "/Users/vigyaawasthi/Documents/Whats happening with my cat?/"
)


def resolve_corpus_dir(configured: Path = PROMPT_CORPUS_DIR) -> Path:
    """Return an existing corpus directory or the configured path for a clear error."""
    if configured.is_dir():
        return configured
    if configured == PROMPT_CORPUS_DIR and WORKSPACE_CORPUS_DIR.is_dir():
        return WORKSPACE_CORPUS_DIR
    return configured
