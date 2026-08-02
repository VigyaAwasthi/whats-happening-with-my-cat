-- Generation traces and generation-referenced feedback.
--
-- A trace explains one answer: what was retrieved at each stage and with what
-- scores, which signals agreed, which models ran, what it cost, how long each
-- stage took, and what the safety gates did. Feedback points at a trace, so a
-- thumbs-down identifies a specific answer rather than a whole conversation.
--
-- Traces contain user content (the query and the answer), so they are:
--   * owned by cat_id and removed by the account delete cascade,
--   * included in account export,
--   * subject to a retention window (TRACE_RETENTION_DAYS, default 90).

CREATE TABLE IF NOT EXISTS generation_traces (
    generation_id uuid PRIMARY KEY,
    cat_id uuid NOT NULL REFERENCES cat_profiles (id) ON DELETE CASCADE,
    session_id uuid NOT NULL,
    corner text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),

    query text NOT NULL DEFAULT '',
    response_text text NOT NULL DEFAULT '',

    -- Per-stage retrieval detail and signal agreement. JSONB because the shape
    -- is a list of ranked rows that is only ever read whole, per generation.
    retrieval jsonb NOT NULL DEFAULT '[]'::jsonb,
    consensus jsonb NOT NULL DEFAULT '{}'::jsonb,

    answer_mode text,
    response_kind text,

    model_calls jsonb NOT NULL DEFAULT '[]'::jsonb,
    prompt_version text NOT NULL DEFAULT 'v1',
    model_call_count integer NOT NULL DEFAULT 0 CHECK (model_call_count >= 0),

    total_input_tokens integer NOT NULL DEFAULT 0 CHECK (total_input_tokens >= 0),
    total_output_tokens integer NOT NULL DEFAULT 0 CHECK (total_output_tokens >= 0),
    cache_read_tokens integer NOT NULL DEFAULT 0 CHECK (cache_read_tokens >= 0),
    cache_write_tokens integer NOT NULL DEFAULT 0 CHECK (cache_write_tokens >= 0),
    cost_usd numeric NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),

    latency jsonb NOT NULL DEFAULT '{}'::jsonb,

    groundedness text NOT NULL DEFAULT 'not_applicable',
    red_flag_fired boolean NOT NULL DEFAULT false,
    red_flag_rules jsonb NOT NULL DEFAULT '[]'::jsonb,
    canned_response_id text,

    CONSTRAINT generation_traces_session_cat_fk
        FOREIGN KEY (session_id, cat_id)
        REFERENCES sessions (id, cat_id)
        ON DELETE CASCADE
);

COMMENT ON TABLE generation_traces IS
    'One row per served answer. Contains user content; covered by account '
    'export, the delete cascade, and TRACE_RETENTION_DAYS.';

-- Retention sweeps and "what happened this week" dashboards both scan by time.
CREATE INDEX IF NOT EXISTS generation_traces_created_at_idx
    ON generation_traces (created_at DESC);
-- Cost and quality dashboards slice by corner over a window.
CREATE INDEX IF NOT EXISTS generation_traces_corner_created_idx
    ON generation_traces (corner, created_at DESC);
-- Export and the cascade both work per cat.
CREATE INDEX IF NOT EXISTS generation_traces_cat_idx
    ON generation_traces (cat_id, created_at DESC);
-- "Which entries are never retrieved" needs to search inside the staged rows.
CREATE INDEX IF NOT EXISTS generation_traces_retrieval_gin
    ON generation_traces USING gin (retrieval jsonb_path_ops);

-- Traces contain the user's query and generated answer, so direct database
-- access must be restricted to the account that owns the associated cat.
ALTER TABLE generation_traces ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS generation_traces_owner_policy
    ON generation_traces;

CREATE POLICY generation_traces_owner_policy ON generation_traces
    FOR ALL
    USING (
        EXISTS (
            SELECT 1
            FROM cat_profiles
            JOIN accounts ON accounts.id = cat_profiles.account_id
            WHERE cat_profiles.id = generation_traces.cat_id
              AND accounts.auth_subject_id = (SELECT auth.uid())
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1
            FROM cat_profiles
            JOIN accounts ON accounts.id = cat_profiles.account_id
            WHERE cat_profiles.id = generation_traces.cat_id
              AND accounts.auth_subject_id = (SELECT auth.uid())
        )
    );

-- ---------------------------------------------------------------------------
-- Feedback: point at the generation, and carry a reason.
-- ---------------------------------------------------------------------------

ALTER TABLE feedback
    ADD COLUMN IF NOT EXISTS generation_id uuid
        REFERENCES generation_traces (generation_id) ON DELETE SET NULL,
    -- A structured reason maps a complaint to a fix: wrong_information and
    -- did_not_answer point at retrieval or the corpus, too_cautious and
    -- not_specific_to_my_cat point at the prompt.
    ADD COLUMN IF NOT EXISTS reason text,
    ADD COLUMN IF NOT EXISTS reason_text text,
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

-- Feedback is editable and revocable, so a user may only hold one live rating
-- per generation. Revocation deletes the row; editing updates it in place.
CREATE UNIQUE INDEX IF NOT EXISTS feedback_generation_unique
    ON feedback (generation_id)
    WHERE generation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS feedback_reason_idx
    ON feedback (reason)
    WHERE reason IS NOT NULL;
