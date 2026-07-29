"""CLI entry point: ``python -m app.ingestion.cli``."""

import argparse
import asyncio
import json
import logging
from pathlib import Path

from app.corpus_paths import PROMPT_CORPUS_DIR, resolve_corpus_dir
from app.db import PostgresDatabase
from app.ingestion.embeddings import (
    DeterministicEmbeddingProvider,
    EmbeddingProvider,
    VoyageEmbeddingProvider,
)
from app.ingestion.pipeline import IngestionPipeline, PostgresCorpusWriter
from app.runtime_config import RuntimeMode, load_runtime_settings


async def _run(source_dir: Path) -> None:
    settings = load_runtime_settings()
    database = PostgresDatabase(settings.database_url.get_secret_value())
    await database.open()
    try:
        if settings.runtime_mode is RuntimeMode.DEVELOPMENT:
            embedder: EmbeddingProvider = DeterministicEmbeddingProvider(
                settings.embedding_dimensions
            )
        else:
            embedder = VoyageEmbeddingProvider(
                api_key=settings.voyage_api_key.get_secret_value(),
                model=settings.embedding_model,
                dimensions=settings.embedding_dimensions,
            )
        report = await IngestionPipeline(
            PostgresCorpusWriter(database), embedder
        ).run(resolve_corpus_dir(source_dir))
        print(json.dumps(report.model_dump(), indent=2))
    finally:
        await database.close()


def main() -> None:
    """Parse CLI arguments and run the ingestion pipeline."""
    parser = argparse.ArgumentParser(description="Ingest curated cat corpora")
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=PROMPT_CORPUS_DIR,
        help="Directory containing the three MASTER CSV files",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run(args.source_dir))


if __name__ == "__main__":
    main()
