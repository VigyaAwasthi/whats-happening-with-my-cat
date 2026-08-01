"""Ingestion contracts, normalization, chunking, and idempotency."""

import csv
from pathlib import Path

import pytest

from app.corpus_paths import resolve_corpus_dir
from app.ingestion.embeddings import DeterministicEmbeddingProvider
from app.ingestion.csv_loader import load_behavior, load_fun_facts, load_health
from app.ingestion.models import ChunkDraft, FunFactIngestRow
from app.ingestion.pipeline import IngestionPipeline
from app.schemas.corpora import BehaviorEntry, HealthEntry
from app.schemas.enums import ConfidenceLevel, UrgencyTier


CORPUS_DIR = resolve_corpus_dir()


class CapturingWriter:
    def __init__(self) -> None:
        self.health: dict[str, HealthEntry] = {}
        self.behavior: dict[str, BehaviorEntry] = {}
        self.facts: dict[str, FunFactIngestRow] = {}
        self.chunks: dict[object, ChunkDraft] = {}

    async def write(
        self,
        health: list[HealthEntry],
        behavior: list[BehaviorEntry],
        facts: list[FunFactIngestRow],
        chunks: list[ChunkDraft],
    ) -> None:
        self.health = {entry.id: entry for entry in health}
        self.behavior = {entry.id: entry for entry in behavior}
        self.facts = {fact.id: fact for fact in facts}
        self.chunks = {chunk.id: chunk for chunk in chunks}


async def test_pipeline_loads_resolved_facts_and_is_idempotent() -> None:
    writer = CapturingWriter()
    pipeline = IngestionPipeline(
        writer, DeterministicEmbeddingProvider(dimensions=1024)
    )
    first = await pipeline.run(CORPUS_DIR)
    sizes = (
        len(writer.health),
        len(writer.behavior),
        len(writer.facts),
        len(writer.chunks),
    )
    second = await pipeline.run(CORPUS_DIR)

    assert first.health_rows == second.health_rows >= 33
    assert first.behavior_rows == second.behavior_rows >= 45
    assert first.fun_fact_rows == second.fun_fact_rows >= 30
    assert first.tags_normalized == second.tags_normalized
    assert first.embedding_failures == second.embedding_failures == 0
    assert sizes == (
        len(writer.health),
        len(writer.behavior),
        len(writer.facts),
        len(writer.chunks),
    )
    assert all(
        not chunk.parent_entry_id.startswith("ff-")
        for chunk in writer.chunks.values()
    )
    assert all(
        chunk.embedding is not None and len(chunk.embedding) == 1024
        for chunk in writer.chunks.values()
    )
    assert len(writer.chunks) == first.chunks_created == second.chunks_created
    health_chunk_count = sum(
        chunk.parent_entry_id in writer.health
        for chunk in writer.chunks.values()
    )
    behavior_chunk_count = sum(
        chunk.parent_entry_id in writer.behavior
        for chunk in writer.chunks.values()
    )
    assert health_chunk_count > len(writer.health)
    assert behavior_chunk_count > len(writer.behavior)


def test_corpus_rows_preserve_required_retrieval_and_grounding_fields() -> None:
    health = load_health(CORPUS_DIR / "MASTER_health_corpus.csv")
    behavior = load_behavior(CORPUS_DIR / "MASTER_behavior_corpus.csv")
    facts, _ = load_fun_facts(CORPUS_DIR / "MASTER_fun_facts.csv")

    for corpus in (health, behavior, facts):
        ids = [entry.id for entry in corpus]
        assert len(ids) == len(set(ids))

    assert all(entry.clarifying_questions for entry in health)
    assert all(entry.clarifying_questions for entry in behavior)
    assert all(entry.sources for entry in [*health, *behavior])
    assert all(
        source.title.strip() and source.organization.strip()
        for entry in [*health, *behavior]
        for source in entry.sources
    )
    assert all(fact.detail.strip() for fact in facts)
    assert all(entry.confidence in set(ConfidenceLevel) for entry in behavior)
    assert all(entry.urgency_tier in set(UrgencyTier) for entry in health)


def test_detail_is_exact_and_general_tag_is_removed() -> None:
    from app.ingestion.csv_loader import load_fun_facts

    source = CORPUS_DIR / "MASTER_fun_facts.csv"
    facts, normalized = load_fun_facts(source)
    with source.open(newline="", encoding="utf-8-sig") as file:
        original = {row["id"]: row for row in csv.DictReader(file)}

    assert normalized == 21
    for fact in facts:
        assert fact.detail == original[fact.id]["detail"]
        assert fact.detail.strip()
        assert "general" not in fact.tags


def test_phase2_migration_fixes_dimension_and_required_detail() -> None:
    migration = (
        Path(__file__).parents[1]
        / "db"
        / "migrations"
        / "003_phase2_resolutions.sql"
    ).read_text(encoding="utf-8")
    assert "vector(1024)" in migration
    assert "ADD COLUMN detail text NOT NULL" in migration
    assert "ALTER TABLE fun_facts DROP COLUMN embedding" in migration


def test_ingestion_rejects_placeholder_source_urls(tmp_path: Path) -> None:
    source = CORPUS_DIR / "MASTER_health_corpus.csv"
    valid_url = next(
        source_ref.url
        for entry in load_health(source)
        for source_ref in entry.sources
        if source_ref.url is not None
    )
    poisoned = source.read_text(encoding="utf-8-sig").replace(
        valid_url,
        valid_url + " [VERIFY exact subpage]",
        1,
    )
    candidate = tmp_path / source.name
    candidate.write_text(poisoned, encoding="utf-8")
    with pytest.raises(ValueError, match="malformed source_1_url"):
        load_health(candidate)
