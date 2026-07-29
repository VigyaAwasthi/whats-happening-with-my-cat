BEGIN;

-- Phase 2 fixes the embedding model at Voyage voyage-3 / 1024 dimensions.
-- Existing embeddings are deliberately cleared: changing vector dimensions
-- requires a migration plus a full re-embed.
DROP INDEX IF EXISTS health_entries_embedding_hnsw_idx;
DROP INDEX IF EXISTS behavior_entries_embedding_hnsw_idx;
DROP INDEX IF EXISTS chunks_embedding_hnsw_idx;
DROP INDEX IF EXISTS long_term_memory_embedding_hnsw_idx;
DROP INDEX IF EXISTS fun_facts_embedding_hnsw_idx;

UPDATE health_entries SET embedding = NULL WHERE embedding IS NOT NULL;
UPDATE behavior_entries SET embedding = NULL WHERE embedding IS NOT NULL;
UPDATE chunks SET embedding = NULL WHERE embedding IS NOT NULL;
UPDATE long_term_memory SET embedding = NULL WHERE embedding IS NOT NULL;

ALTER TABLE health_entries
    ALTER COLUMN embedding TYPE vector(1024);
ALTER TABLE behavior_entries
    ALTER COLUMN embedding TYPE vector(1024);
ALTER TABLE chunks
    ALTER COLUMN embedding TYPE vector(1024);
ALTER TABLE long_term_memory
    ALTER COLUMN embedding TYPE vector(1024);

CREATE INDEX health_entries_embedding_hnsw_idx
    ON health_entries USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;
CREATE INDEX behavior_entries_embedding_hnsw_idx
    ON behavior_entries USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;
CREATE INDEX chunks_embedding_hnsw_idx
    ON chunks USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;
CREATE INDEX long_term_memory_embedding_hnsw_idx
    ON long_term_memory USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

-- Fun facts are curated flat records selected by tags. They are never embedded.
ALTER TABLE fun_facts DROP COLUMN embedding;
ALTER TABLE fun_facts
    ADD COLUMN detail text NOT NULL
        CHECK (length(btrim(detail)) > 0);

COMMIT;

