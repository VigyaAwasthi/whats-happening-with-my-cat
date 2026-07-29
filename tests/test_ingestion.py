"""Ingestion contracts, normalization, chunking, and idempotency."""

import csv
from pathlib import Path

from app.corpus_paths import resolve_corpus_dir
from app.ingestion.embeddings import DeterministicEmbeddingProvider
from app.ingestion.models import ChunkDraft
from app.ingestion.pipeline import IngestionPipeline


CORPUS_DIR = resolve_corpus_dir()


class CapturingWriter:
    def __init__(self) -> None:
        self.health: dict[str, object] = {}
        self.behavior: dict[str, object] = {}
        self.facts: dict[str, object] = {}
        self.chunks: dict[object, ChunkDraft] = {}

    async def write(
        self,
        health: list[object],
        behavior: list[object],
        facts: list[object],
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

    assert first.health_rows == second.health_rows == 35
    assert first.behavior_rows == second.behavior_rows == 17
    assert first.fun_fact_rows == second.fun_fact_rows == 34
    assert first.tags_normalized == second.tags_normalized == 21
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
    assert len(writer.chunks) == first.chunks_created == 156


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
