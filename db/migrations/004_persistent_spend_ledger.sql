-- Persistent, atomic application-wide LLM spend accounting.
-- The service role owns this operational table; it contains no user data.

CREATE TABLE llm_spend_totals (
    budget_key text PRIMARY KEY,
    spent_usd numeric NOT NULL DEFAULT 0
        CHECK (spent_usd >= 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE llm_spend_totals IS
    'Cumulative LLM spend reservations shared across processes and restarts.';
