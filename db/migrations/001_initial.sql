BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TYPE age_unit AS ENUM ('months', 'years');
CREATE TYPE weight_unit AS ENUM ('kg', 'lb');
CREATE TYPE corner_name AS ENUM (
    'behavior',
    'health',
    'fun-facts',
    'special-moments'
);
CREATE TYPE message_role AS ENUM ('user', 'assistant');
CREATE TYPE moment_kind AS ENUM ('photo', 'video', 'note', 'date');
CREATE TYPE feedback_thumb AS ENUM ('up', 'down');
CREATE TYPE urgency_tier AS ENUM ('emergency', 'urgent', 'monitor', 'routine');
CREATE TYPE body_system AS ENUM (
    'dental',
    'digestive',
    'ears',
    'eyes',
    'kidney',
    'musculoskeletal',
    'neurological',
    'respiratory',
    'skin',
    'systemic',
    'toxin',
    'urinary'
);
CREATE TYPE behavior_category AS ENUM (
    'environment',
    'social',
    'communication',
    'stress-signals',
    'normal-behavior'
);
CREATE TYPE confidence_level AS ENUM (
    'well-established',
    'general',
    'varies-by-cat'
);
CREATE TYPE fun_fact_category AS ENUM (
    'age',
    'behavior',
    'breed',
    'coat',
    'cognition',
    'communication',
    'history',
    'senses'
);
CREATE TYPE fun_fact_tone AS ENUM ('playful', 'informative');
CREATE TYPE corpus_kind AS ENUM ('health', 'behavior', 'fun-fact');

CREATE FUNCTION immutable_text_array_to_string(values_to_join text[])
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT coalesce(array_to_string(values_to_join, ' '), '');
$$;

CREATE TABLE accounts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    auth_subject_id uuid NOT NULL UNIQUE,
    preferences jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT accounts_preferences_object
        CHECK (jsonb_typeof(preferences) = 'object')
);

CREATE TABLE cat_profiles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL
        REFERENCES accounts(id) ON DELETE CASCADE,
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 100),
    age_value double precision NOT NULL CHECK (age_value >= 0),
    age_unit age_unit NOT NULL,
    breed text,
    weight_value double precision NOT NULL CHECK (weight_value > 0),
    weight_unit weight_unit NOT NULL,
    energy_level smallint NOT NULL CHECK (energy_level BETWEEN 1 AND 5),
    common_patterns text NOT NULL,
    known_conditions text[] NOT NULL DEFAULT ARRAY[]::text[],
    photo_references text[] NOT NULL DEFAULT ARRAY[]::text[],
    theme jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cat_profiles_theme_object CHECK (jsonb_typeof(theme) = 'object'),
    CONSTRAINT cat_profiles_account_and_id_unique UNIQUE (account_id, id)
);

CREATE INDEX cat_profiles_account_id_idx ON cat_profiles(account_id);

CREATE FUNCTION enforce_max_ten_cats_per_account()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.account_id = NEW.account_id THEN
        RETURN NEW;
    END IF;

    -- Serialize creates per account so concurrent requests cannot exceed the cap.
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.account_id::text, 0));

    IF (
        SELECT count(*)
        FROM cat_profiles
        WHERE account_id = NEW.account_id
    ) >= 10 THEN
        RAISE EXCEPTION 'an account may own at most 10 cats'
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER cat_profiles_max_ten_per_account
BEFORE INSERT OR UPDATE OF account_id ON cat_profiles
FOR EACH ROW
EXECUTE FUNCTION enforce_max_ten_cats_per_account();

-- A registry supplies one real foreign-key target for chunks across three
-- differently shaped corpus tables. Corpus slugs are globally unique.
CREATE TABLE corpus_entries (
    id text PRIMARY KEY,
    kind corpus_kind NOT NULL
);

CREATE TABLE health_entries (
    id text PRIMARY KEY
        REFERENCES corpus_entries(id) ON DELETE CASCADE,
    topic text NOT NULL,
    body_system body_system NOT NULL,
    aliases text[] NOT NULL,
    keywords text[] NOT NULL,
    summary text NOT NULL,
    urgency_tier urgency_tier NOT NULL,
    red_flags text[] NOT NULL,
    when_to_see_vet text NOT NULL,
    clarifying_questions text[] NOT NULL,
    related_topics text[] NOT NULL DEFAULT ARRAY[]::text[],
    related_conditions text[] NOT NULL DEFAULT ARRAY[]::text[],
    sources jsonb NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector(
            'english'::regconfig,
            coalesce(summary, '') || ' ' ||
            immutable_text_array_to_string(aliases) || ' ' ||
            immutable_text_array_to_string(keywords)
        )
    ) STORED,
    -- Phase 1 baseline; migration 003 converts all retrievable vectors to 1024.
    embedding vector(1536),
    CONSTRAINT health_entries_aliases_nonempty CHECK (cardinality(aliases) > 0),
    CONSTRAINT health_entries_keywords_nonempty CHECK (cardinality(keywords) > 0),
    CONSTRAINT health_entries_red_flags_nonempty CHECK (cardinality(red_flags) > 0),
    CONSTRAINT health_entries_questions_nonempty
        CHECK (cardinality(clarifying_questions) > 0),
    CONSTRAINT health_entries_sources_array
        CHECK (
            jsonb_typeof(sources) = 'array'
            AND jsonb_array_length(sources) BETWEEN 1 AND 3
        )
);

CREATE INDEX health_entries_search_vector_idx
    ON health_entries USING gin(search_vector);
CREATE INDEX health_entries_embedding_hnsw_idx
    ON health_entries USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

CREATE TABLE behavior_entries (
    id text PRIMARY KEY
        REFERENCES corpus_entries(id) ON DELETE CASCADE,
    topic text NOT NULL,
    category behavior_category NOT NULL,
    aliases text[] NOT NULL,
    keywords text[] NOT NULL,
    summary text NOT NULL,
    confidence confidence_level NOT NULL,
    medical_flag text[] NOT NULL DEFAULT ARRAY[]::text[],
    clarifying_questions text[] NOT NULL,
    related_topics text[] NOT NULL DEFAULT ARRAY[]::text[],
    sources jsonb NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector(
            'english'::regconfig,
            coalesce(summary, '') || ' ' ||
            immutable_text_array_to_string(aliases) || ' ' ||
            immutable_text_array_to_string(keywords)
        )
    ) STORED,
    embedding vector(1536),
    CONSTRAINT behavior_entries_aliases_nonempty CHECK (cardinality(aliases) > 0),
    CONSTRAINT behavior_entries_keywords_nonempty CHECK (cardinality(keywords) > 0),
    CONSTRAINT behavior_entries_questions_nonempty
        CHECK (cardinality(clarifying_questions) > 0),
    CONSTRAINT behavior_entries_sources_array
        CHECK (
            jsonb_typeof(sources) = 'array'
            AND jsonb_array_length(sources) BETWEEN 1 AND 2
        )
);

CREATE INDEX behavior_entries_search_vector_idx
    ON behavior_entries USING gin(search_vector);
CREATE INDEX behavior_entries_embedding_hnsw_idx
    ON behavior_entries USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

CREATE TABLE fun_facts (
    id text PRIMARY KEY
        REFERENCES corpus_entries(id) ON DELETE CASCADE,
    fact text NOT NULL,
    category fun_fact_category NOT NULL,
    tags text[] NOT NULL,
    tone fun_fact_tone NOT NULL,
    personalization_hook text NOT NULL,
    source_note text NOT NULL,
    source_url text,
    -- Migration 003 adds required curated detail and removes this unused embedding.
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector(
            'english'::regconfig,
            coalesce(fact, '') || ' ' ||
            immutable_text_array_to_string(tags)
        )
    ) STORED,
    embedding vector(1536),
    CONSTRAINT fun_facts_tags_nonempty CHECK (cardinality(tags) > 0)
);

CREATE INDEX fun_facts_search_vector_idx
    ON fun_facts USING gin(search_vector);
CREATE INDEX fun_facts_embedding_hnsw_idx
    ON fun_facts USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

CREATE TABLE chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_entry_id text NOT NULL
        REFERENCES corpus_entries(id) ON DELETE CASCADE,
    chunk_text text NOT NULL CHECK (length(btrim(chunk_text)) > 0),
    embedding vector(1536)
);

CREATE INDEX chunks_parent_entry_id_idx ON chunks(parent_entry_id);
CREATE INDEX chunks_embedding_hnsw_idx
    ON chunks USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

CREATE TABLE sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cat_id uuid NOT NULL
        REFERENCES cat_profiles(id) ON DELETE CASCADE,
    corner corner_name NOT NULL,
    rolling_summary text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT sessions_id_and_cat_unique UNIQUE (id, cat_id)
);

COMMENT ON TABLE sessions IS
    'Cat-isolated memory: every read must filter by non-null cat_id.';
CREATE INDEX sessions_cat_id_idx ON sessions(cat_id);

CREATE TABLE session_messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id uuid NOT NULL,
    cat_id uuid NOT NULL
        REFERENCES cat_profiles(id) ON DELETE CASCADE,
    role message_role NOT NULL,
    content text NOT NULL CHECK (length(btrim(content)) > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT session_messages_session_cat_fk
        FOREIGN KEY (session_id, cat_id)
        REFERENCES sessions(id, cat_id)
        ON DELETE CASCADE
);

COMMENT ON TABLE session_messages IS
    'Cat-isolated memory: every read must filter by non-null cat_id.';
CREATE INDEX session_messages_cat_id_idx ON session_messages(cat_id);
CREATE INDEX session_messages_session_id_idx ON session_messages(session_id);

CREATE TABLE long_term_memory (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cat_id uuid NOT NULL
        REFERENCES cat_profiles(id) ON DELETE CASCADE,
    summary text NOT NULL CHECK (length(btrim(summary)) > 0),
    source_session_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    embedding vector(1536),
    CONSTRAINT long_term_memory_session_cat_fk
        FOREIGN KEY (source_session_id, cat_id)
        REFERENCES sessions(id, cat_id)
        ON DELETE CASCADE
);

COMMENT ON TABLE long_term_memory IS
    'Cat-isolated memory: every read must filter by non-null cat_id.';
CREATE INDEX long_term_memory_cat_id_idx ON long_term_memory(cat_id);
CREATE INDEX long_term_memory_embedding_hnsw_idx
    ON long_term_memory USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

CREATE TABLE moments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cat_id uuid NOT NULL
        REFERENCES cat_profiles(id) ON DELETE CASCADE,
    kind moment_kind NOT NULL,
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 200),
    body text,
    media_key text,
    event_date date,
    created_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE moments IS
    'Cat-isolated scrapbook: every read must filter by non-null cat_id; AI must never retrieve this table.';
CREATE INDEX moments_cat_id_idx ON moments(cat_id);

CREATE TABLE feedback (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cat_id uuid NOT NULL
        REFERENCES cat_profiles(id) ON DELETE CASCADE,
    session_id uuid NOT NULL,
    corner corner_name NOT NULL,
    thumb feedback_thumb NOT NULL,
    helpfulness_score smallint
        CHECK (helpfulness_score BETWEEN 1 AND 5),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT feedback_session_cat_fk
        FOREIGN KEY (session_id, cat_id)
        REFERENCES sessions(id, cat_id)
        ON DELETE CASCADE
);

CREATE INDEX feedback_cat_id_idx ON feedback(cat_id);
CREATE INDEX feedback_session_id_idx ON feedback(session_id);

-- Supabase provides auth.uid(). A fresh PostgreSQL validation database does
-- not, so create a compatible shim only when the platform function is absent.
CREATE SCHEMA IF NOT EXISTS auth;
DO $$
BEGIN
    IF to_regprocedure('auth.uid()') IS NULL THEN
        EXECUTE $function$
            CREATE FUNCTION auth.uid()
            RETURNS uuid
            LANGUAGE sql
            STABLE
            AS $body$
                SELECT nullif(
                    current_setting('request.jwt.claim.sub', true),
                    ''
                )::uuid
            $body$
        $function$;
    END IF;
END;
$$;

ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE cat_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE session_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE long_term_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE moments ENABLE ROW LEVEL SECURITY;
ALTER TABLE feedback ENABLE ROW LEVEL SECURITY;

-- Supabase policy stubs. These use the authenticated JWT subject exposed by auth.uid().
CREATE POLICY accounts_owner_policy ON accounts
    FOR ALL
    USING (auth_subject_id = (SELECT auth.uid()))
    WITH CHECK (auth_subject_id = (SELECT auth.uid()));

CREATE POLICY cat_profiles_owner_policy ON cat_profiles
    FOR ALL
    USING (
        EXISTS (
            SELECT 1
            FROM accounts
            WHERE accounts.id = cat_profiles.account_id
              AND accounts.auth_subject_id = (SELECT auth.uid())
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1
            FROM accounts
            WHERE accounts.id = cat_profiles.account_id
              AND accounts.auth_subject_id = (SELECT auth.uid())
        )
    );

CREATE POLICY sessions_owner_policy ON sessions
    FOR ALL
    USING (
        EXISTS (
            SELECT 1
            FROM cat_profiles
            JOIN accounts ON accounts.id = cat_profiles.account_id
            WHERE cat_profiles.id = sessions.cat_id
              AND accounts.auth_subject_id = (SELECT auth.uid())
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1
            FROM cat_profiles
            JOIN accounts ON accounts.id = cat_profiles.account_id
            WHERE cat_profiles.id = sessions.cat_id
              AND accounts.auth_subject_id = (SELECT auth.uid())
        )
    );

CREATE POLICY session_messages_owner_policy ON session_messages
    FOR ALL
    USING (
        EXISTS (
            SELECT 1
            FROM cat_profiles
            JOIN accounts ON accounts.id = cat_profiles.account_id
            WHERE cat_profiles.id = session_messages.cat_id
              AND accounts.auth_subject_id = (SELECT auth.uid())
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1
            FROM cat_profiles
            JOIN accounts ON accounts.id = cat_profiles.account_id
            WHERE cat_profiles.id = session_messages.cat_id
              AND accounts.auth_subject_id = (SELECT auth.uid())
        )
    );

CREATE POLICY long_term_memory_owner_policy ON long_term_memory
    FOR ALL
    USING (
        EXISTS (
            SELECT 1
            FROM cat_profiles
            JOIN accounts ON accounts.id = cat_profiles.account_id
            WHERE cat_profiles.id = long_term_memory.cat_id
              AND accounts.auth_subject_id = (SELECT auth.uid())
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1
            FROM cat_profiles
            JOIN accounts ON accounts.id = cat_profiles.account_id
            WHERE cat_profiles.id = long_term_memory.cat_id
              AND accounts.auth_subject_id = (SELECT auth.uid())
        )
    );

CREATE POLICY moments_owner_policy ON moments
    FOR ALL
    USING (
        EXISTS (
            SELECT 1
            FROM cat_profiles
            JOIN accounts ON accounts.id = cat_profiles.account_id
            WHERE cat_profiles.id = moments.cat_id
              AND accounts.auth_subject_id = (SELECT auth.uid())
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1
            FROM cat_profiles
            JOIN accounts ON accounts.id = cat_profiles.account_id
            WHERE cat_profiles.id = moments.cat_id
              AND accounts.auth_subject_id = (SELECT auth.uid())
        )
    );

CREATE POLICY feedback_owner_policy ON feedback
    FOR ALL
    USING (
        EXISTS (
            SELECT 1
            FROM cat_profiles
            JOIN accounts ON accounts.id = cat_profiles.account_id
            WHERE cat_profiles.id = feedback.cat_id
              AND accounts.auth_subject_id = (SELECT auth.uid())
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1
            FROM cat_profiles
            JOIN accounts ON accounts.id = cat_profiles.account_id
            WHERE cat_profiles.id = feedback.cat_id
              AND accounts.auth_subject_id = (SELECT auth.uid())
        )
    );

COMMIT;
