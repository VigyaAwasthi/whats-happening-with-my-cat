-- Windowed LLM spend accounting.
--
-- Migration 004 created `llm_spend_totals` with a single cumulative row under
-- the key 'global'. Once the cap was reached the system stayed disabled until
-- an operator cleared the table by hand.
--
-- The window is now encoded in `budget_key` itself:
--
--   monthly   ->  'global:YYYY-MM'   (UTC calendar month, boundary 00:00 UTC
--                                     on the first of the month)
--   lifetime  ->  'global'           (the original cumulative behavior)
--
-- Nothing runs at the boundary. A new period simply accumulates against a new
-- row, so a cap reached in one month stops blocking calls the moment the next
-- month's key comes into use. Old rows are retained as the spend history.
--
-- This migration is additive and idempotent: no existing row is rewritten, and
-- the pre-existing 'global' row remains valid under SPEND_WINDOW=lifetime.

CREATE INDEX IF NOT EXISTS llm_spend_totals_updated_at_idx
    ON llm_spend_totals (updated_at DESC);

COMMENT ON TABLE llm_spend_totals IS
    'LLM spend per accounting window. budget_key is ''global'' for the lifetime '
    'window or ''global:YYYY-MM'' for the UTC-calendar-month window. Rows are '
    'append-only history; inspect and reset with `python -m app.ops.spend`.';

COMMENT ON COLUMN llm_spend_totals.budget_key IS
    'Accounting window identifier; see the table comment for the key format.';
