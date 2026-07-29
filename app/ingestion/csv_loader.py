"""Strict CSV parsing into Phase 1 contracts plus resolved fun-fact detail."""

import csv
from pathlib import Path

from app.ingestion.models import FunFactIngestRow
from app.schemas.corpora import BehaviorEntry, HealthEntry


PIPE_DELIMITER = " | "


def split_pipe(value: str) -> list[str]:
    """Split only the corpus's exact delimiter and discard empty cells."""
    stripped = value.strip()
    return [] if not stripped else stripped.split(PIPE_DELIMITER)


def load_health(path: Path) -> list[HealthEntry]:
    """Load every health row and fail loudly on schema drift."""
    rows = _read_rows(path)
    return [
        HealthEntry.model_validate(
            {
                "id": row["id"],
                "topic": row["topic"],
                "body_system": row["body_system"],
                "aliases": split_pipe(row["aliases"]),
                "keywords": split_pipe(row["keywords"]),
                "summary": row["summary"],
                "urgency_tier": row["urgency_tier"],
                "red_flags": split_pipe(row["red_flags"]),
                "when_to_see_vet": row["when_to_see_vet"],
                "clarifying_questions": split_pipe(row["clarifying_questions"]),
                "related_topics": split_pipe(row["related_topics"]),
                "related_conditions": split_pipe(row["related_conditions"]),
                "sources": _sources(row, 3),
            }
        )
        for row in rows
    ]


def load_behavior(path: Path) -> list[BehaviorEntry]:
    """Load every behavior row and fail loudly on schema drift."""
    rows = _read_rows(path)
    return [
        BehaviorEntry.model_validate(
            {
                "id": row["id"],
                "topic": row["topic"],
                "category": row["category"],
                "aliases": split_pipe(row["aliases"]),
                "keywords": split_pipe(row["keywords"]),
                "summary": row["summary"],
                "confidence": row["confidence"],
                "medical_flag": split_pipe(row["medical_flag"]),
                "clarifying_questions": split_pipe(row["clarifying_questions"]),
                "related_topics": split_pipe(row["related_topics"]),
                "sources": _sources(row, 2),
            }
        )
        for row in rows
    ]


def load_fun_facts(path: Path) -> tuple[list[FunFactIngestRow], int]:
    """Load facts, requiring detail and normalizing redundant general tags."""
    rows = _read_rows(path)
    expected = [
        "id",
        "fact",
        "detail",
        "category",
        "tags",
        "tone",
        "personalization_hook",
        "source_note",
        "source_url",
    ]
    if list(rows[0]) != expected:
        raise ValueError(f"unexpected fun-fact columns: {list(rows[0])!r}")

    facts: list[FunFactIngestRow] = []
    normalized_count = 0
    for row in rows:
        detail = row["detail"]
        if not detail.strip():
            raise ValueError(f"fun fact {row['id']!r} has empty detail")
        tags = split_pipe(row["tags"])
        if "general" in tags:
            tags = [tag for tag in tags if tag != "general"]
            normalized_count += 1
        facts.append(
            FunFactIngestRow.model_validate(
                {
                    "id": row["id"],
                    "fact": row["fact"],
                    "detail": detail,
                    "category": row["category"],
                    "tags": tags,
                    "tone": row["tone"],
                    "personalization_hook": row["personalization_hook"],
                    "source_note": row["source_note"],
                    "source_url": row["source_url"] or None,
                }
            )
        )
    return facts, normalized_count


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    if not rows:
        raise ValueError(f"corpus file is empty: {path}")
    if any(None in row for row in rows):
        raise ValueError(f"corpus row has extra unmapped columns: {path}")
    return rows


def _sources(row: dict[str, str], maximum: int) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    for index in range(1, maximum + 1):
        title = row[f"source_{index}_title"]
        organization = row[f"source_{index}_org"]
        url = row[f"source_{index}_url"]
        if not title and not organization and not url:
            continue
        if not title or not organization or not url:
            raise ValueError(f"incomplete source {index} on entry {row['id']!r}")
        sources.append(
            {"title": title, "organization": organization, "url": url}
        )
    return sources

