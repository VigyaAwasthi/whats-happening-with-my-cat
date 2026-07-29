"""Deterministic semantic-boundary chunk construction."""

from uuid import NAMESPACE_URL, uuid5

from app.ingestion.models import ChunkDraft
from app.schemas.corpora import BehaviorEntry, HealthEntry


def health_chunks(entry: HealthEntry) -> list[ChunkDraft]:
    """Create complete-field chunks without splitting a curated sentence."""
    parts: list[tuple[str, str]] = [("summary", entry.summary)]
    if entry.clarifying_questions:
        parts.append(
            (
                "clarifying-questions",
                "Clarifying questions: " + " ".join(entry.clarifying_questions),
            )
        )
    if entry.related_conditions:
        parts.append(
            (
                "related-conditions",
                "Related conditions: " + " ".join(entry.related_conditions),
            )
        )
    if entry.when_to_see_vet:
        parts.append(("when-to-see-vet", entry.when_to_see_vet))
    return _build(entry.id, entry.aliases, entry.keywords, parts)


def behavior_chunks(entry: BehaviorEntry) -> list[ChunkDraft]:
    """Create summary and curated-question child chunks."""
    parts: list[tuple[str, str]] = [("summary", entry.summary)]
    if entry.clarifying_questions:
        parts.append(
            (
                "clarifying-questions",
                "Clarifying questions: " + " ".join(entry.clarifying_questions),
            )
        )
    return _build(entry.id, entry.aliases, entry.keywords, parts)


def _build(
    parent_id: str,
    aliases: list[str],
    keywords: list[str],
    parts: list[tuple[str, str]],
) -> list[ChunkDraft]:
    vocabulary = " ".join([*aliases, *keywords])
    return [
        ChunkDraft(
            id=uuid5(NAMESPACE_URL, f"cat-corpus:{parent_id}:{label}"),
            parent_entry_id=parent_id,
            chunk_text=text,
            embedding_text=f"{text}\nAliases and keywords: {vocabulary}",
        )
        for label, text in parts
        if text.strip()
    ]

