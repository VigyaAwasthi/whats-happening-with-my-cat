"""End-to-end idempotent corpus ingestion."""

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, TypeVar

from app.db import Database, vector_literal
from app.ingestion.chunking import behavior_chunks, health_chunks
from app.ingestion.csv_loader import load_behavior, load_fun_facts, load_health
from app.ingestion.embeddings import EmbeddingProvider
from app.ingestion.models import ChunkDraft, FunFactIngestRow, IngestionReport
from app.schemas.corpora import BehaviorEntry, HealthEntry

logger = logging.getLogger(__name__)


class EntryWithID(Protocol):
    id: str


EntryT = TypeVar("EntryT", bound=EntryWithID)


class CorpusWriter(Protocol):
    """Persistence boundary for one atomic corpus replacement."""

    async def write(
        self,
        health: list[HealthEntry],
        behavior: list[BehaviorEntry],
        facts: list[FunFactIngestRow],
        chunks: list[ChunkDraft],
    ) -> None:
        """Upsert parents/facts and replace child chunks idempotently."""
        ...


class IngestionPipeline:
    """Strict loader, chunker, embedder, and idempotent writer."""

    def __init__(self, writer: CorpusWriter, embedder: EmbeddingProvider) -> None:
        self._writer = writer
        self._embedder = embedder

    async def run(self, source_dir: Path) -> IngestionReport:
        health = _unique_by_id(
            load_health(source_dir / "MASTER_health_corpus.csv"), "health"
        )
        behavior = _unique_by_id(
            load_behavior(source_dir / "MASTER_behavior_corpus.csv"), "behavior"
        )
        facts, normalized = load_fun_facts(source_dir / "MASTER_fun_facts.csv")
        facts = _unique_by_id(facts, "fun-fact")
        _require_global_id_uniqueness(health, behavior, facts)

        chunks = [
            *(
                chunk
                for health_entry in health
                for chunk in health_chunks(health_entry)
            ),
            *(
                chunk
                for behavior_entry in behavior
                for chunk in behavior_chunks(behavior_entry)
            ),
        ]
        batch = await self._embedder.embed_documents(
            [chunk.embedding_text for chunk in chunks]
        )
        if len(batch.vectors) != len(chunks):
            raise ValueError("embedder result count does not match chunk count")
        embedded_chunks = [
            chunk.model_copy(update={"embedding": vector})
            for chunk, vector in zip(chunks, batch.vectors, strict=True)
        ]
        await self._writer.write(health, behavior, facts, embedded_chunks)
        return IngestionReport(
            health_rows=len(health),
            behavior_rows=len(behavior),
            fun_fact_rows=len(facts),
            chunks_created=len(embedded_chunks),
            tags_normalized=normalized,
            embedding_failures=batch.failures,
        )


def _unique_by_id(entries: list[EntryT], corpus_name: str) -> list[EntryT]:
    """Use deterministic last-row-wins semantics while surfacing source collisions."""
    by_id: dict[str, EntryT] = {}
    duplicates: list[str] = []
    for entry in entries:
        if entry.id in by_id:
            duplicates.append(entry.id)
        by_id[entry.id] = entry
    if duplicates:
        logger.warning(
            "%s corpus has duplicate stable ids; last row wins: %s",
            corpus_name,
            ", ".join(dict.fromkeys(duplicates)),
        )
    return list(by_id.values())


def _require_global_id_uniqueness(*corpora: Sequence[EntryWithID]) -> None:
    """Reject ids reused across corpus kinds because the registry is global."""
    seen: dict[str, int] = {}
    for corpus_index, entries in enumerate(corpora):
        for entry in entries:
            previous = seen.setdefault(entry.id, corpus_index)
            if previous != corpus_index:
                raise ValueError(
                    f"entry id {entry.id!r} is reused across corpus kinds"
                )


class PostgresCorpusWriter:
    """PostgreSQL upserts with parent/chunk replacement in one transaction."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def write(
        self,
        health: list[HealthEntry],
        behavior: list[BehaviorEntry],
        facts: list[FunFactIngestRow],
        chunks: list[ChunkDraft],
    ) -> None:
        async with self._database.transaction() as database:
            for health_entry in health:
                await self._upsert_registry(
                    database, health_entry.id, "health"
                )
                await database.execute(
                    _HEALTH_UPSERT, _health_params(health_entry)
                )
            for behavior_entry in behavior:
                await self._upsert_registry(
                    database, behavior_entry.id, "behavior"
                )
                await database.execute(
                    _BEHAVIOR_UPSERT, _behavior_params(behavior_entry)
                )
            for fact in facts:
                await self._upsert_registry(database, fact.id, "fun-fact")
                await database.execute(_FUN_FACT_UPSERT, _fact_params(fact))

            parent_ids = [entry.id for entry in [*health, *behavior]]
            await database.execute(
                "DELETE FROM chunks WHERE parent_entry_id = ANY(%s)",
                (parent_ids,),
            )
            for chunk in chunks:
                await database.execute(
                    """
                    INSERT INTO chunks (id, parent_entry_id, chunk_text, embedding)
                    VALUES (%s, %s, %s, %s::vector)
                    ON CONFLICT (id) DO UPDATE SET
                        parent_entry_id = EXCLUDED.parent_entry_id,
                        chunk_text = EXCLUDED.chunk_text,
                        embedding = EXCLUDED.embedding
                    """,
                    (
                        chunk.id,
                        chunk.parent_entry_id,
                        chunk.chunk_text,
                        None
                        if chunk.embedding is None
                        else vector_literal(chunk.embedding),
                    ),
                )

    async def _upsert_registry(
        self, database: Database, entry_id: str, kind: str
    ) -> None:
        await database.execute(
            """
            INSERT INTO corpus_entries (id, kind)
            VALUES (%s, %s)
            ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind
            """,
            (entry_id, kind),
        )


def _health_params(entry: HealthEntry) -> tuple[object, ...]:
    return (
        entry.id,
        entry.topic,
        entry.body_system.value,
        entry.aliases,
        entry.keywords,
        entry.summary,
        entry.urgency_tier.value,
        entry.red_flags,
        entry.when_to_see_vet,
        entry.clarifying_questions,
        entry.related_topics,
        entry.related_conditions,
        json.dumps([source.model_dump(mode="json") for source in entry.sources]),
    )


def _behavior_params(entry: BehaviorEntry) -> tuple[object, ...]:
    return (
        entry.id,
        entry.topic,
        entry.category.value,
        entry.aliases,
        entry.keywords,
        entry.summary,
        entry.confidence.value,
        entry.medical_flag,
        entry.clarifying_questions,
        entry.related_topics,
        json.dumps([source.model_dump(mode="json") for source in entry.sources]),
    )


def _fact_params(fact: FunFactIngestRow) -> tuple[object, ...]:
    return (
        fact.id,
        fact.fact,
        fact.detail,
        fact.category.value,
        fact.tags,
        fact.tone.value,
        fact.personalization_hook,
        fact.source_note,
        fact.source_url,
    )


_HEALTH_UPSERT = """
INSERT INTO health_entries (
    id, topic, body_system, aliases, keywords, summary, urgency_tier,
    red_flags, when_to_see_vet, clarifying_questions, related_topics,
    related_conditions, sources
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT (id) DO UPDATE SET
    topic = EXCLUDED.topic,
    body_system = EXCLUDED.body_system,
    aliases = EXCLUDED.aliases,
    keywords = EXCLUDED.keywords,
    summary = EXCLUDED.summary,
    urgency_tier = EXCLUDED.urgency_tier,
    red_flags = EXCLUDED.red_flags,
    when_to_see_vet = EXCLUDED.when_to_see_vet,
    clarifying_questions = EXCLUDED.clarifying_questions,
    related_topics = EXCLUDED.related_topics,
    related_conditions = EXCLUDED.related_conditions,
    sources = EXCLUDED.sources
"""

_BEHAVIOR_UPSERT = """
INSERT INTO behavior_entries (
    id, topic, category, aliases, keywords, summary, confidence,
    medical_flag, clarifying_questions, related_topics, sources
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT (id) DO UPDATE SET
    topic = EXCLUDED.topic,
    category = EXCLUDED.category,
    aliases = EXCLUDED.aliases,
    keywords = EXCLUDED.keywords,
    summary = EXCLUDED.summary,
    confidence = EXCLUDED.confidence,
    medical_flag = EXCLUDED.medical_flag,
    clarifying_questions = EXCLUDED.clarifying_questions,
    related_topics = EXCLUDED.related_topics,
    sources = EXCLUDED.sources
"""

_FUN_FACT_UPSERT = """
INSERT INTO fun_facts (
    id, fact, detail, category, tags, tone, personalization_hook,
    source_note, source_url
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    fact = EXCLUDED.fact,
    detail = EXCLUDED.detail,
    category = EXCLUDED.category,
    tags = EXCLUDED.tags,
    tone = EXCLUDED.tone,
    personalization_hook = EXCLUDED.personalization_hook,
    source_note = EXCLUDED.source_note,
    source_url = EXCLUDED.source_url
"""
