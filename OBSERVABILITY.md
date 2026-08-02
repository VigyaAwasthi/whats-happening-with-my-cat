# Observability

Everything below is answerable from two tables — `generation_traces`
(migration 009) and `llm_spend_totals` (migrations 004/008) — plus the
structured logs. No dashboard product is required; the queries are the
dashboard. See [§7](#7-should-we-add-a-vendor-tool) for the recommendation on
whether to change that.

Run these against the production `DATABASE_URL`. The CLIs
(`python -m app.ops.traces`, `python -m app.ops.spend`) wrap the ones you reach
for daily.

---

## Contents

1. [Cost](#1-cost)
2. [Quality](#2-quality)
3. [Safety](#3-safety)
4. [Reliability](#4-reliability)
5. [Corpus](#5-corpus)
6. [Alerting](#6-alerting)
7. [Should we add a vendor tool?](#7-should-we-add-a-vendor-tool)
8. [Retention](#8-retention)

---

## 1. Cost

### Spend today, this week, this month, against the cap

```sql
SELECT
  count(*)                                                   AS answers,
  round(sum(cost_usd) FILTER (WHERE created_at >= current_date), 6)         AS today,
  round(sum(cost_usd) FILTER (WHERE created_at >= now() - interval '7 days'), 6) AS week,
  round(sum(cost_usd) FILTER (WHERE created_at >= date_trunc('month', now())), 6) AS month
FROM generation_traces;
```

The authoritative cap position is the ledger, not this table — the ledger holds
conservative pre-call reservations, so it is deliberately ahead of realised
cost:

```bash
python -m app.ops.spend show
```

### Cost per query by corner

```sql
SELECT corner,
       count(*)                     AS answers,
       round(avg(cost_usd), 6)      AS avg_cost,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY cost_usd)::numeric, 6) AS p95_cost,
       round(sum(cost_usd), 4)      AS total
FROM generation_traces
WHERE created_at >= now() - interval '7 days'
GROUP BY corner
ORDER BY total DESC;
```

### Cache hit rate

Prompt caching is the largest single lever on cost here. A collapse in this
number usually means a system prompt changed and invalidated the cached prefix.

```sql
SELECT corner,
       sum(cache_read_tokens)                                        AS cache_reads,
       sum(total_input_tokens + cache_read_tokens + cache_write_tokens) AS all_input,
       round(100.0 * sum(cache_read_tokens)
             / nullif(sum(total_input_tokens + cache_read_tokens + cache_write_tokens), 0), 1)
         AS cache_hit_pct
FROM generation_traces
WHERE created_at >= now() - interval '7 days'
GROUP BY corner;
```

### Which call site dominates spend

`model_calls` is JSONB, one object per call, so the call sites unnest:

```sql
SELECT call->>'purpose'                          AS call_site,
       call->>'model'                            AS model,
       count(*)                                  AS calls,
       round(sum((call->>'cost_usd')::numeric), 4) AS spend,
       round(avg((call->>'input_tokens')::numeric))  AS avg_in,
       round(avg((call->>'output_tokens')::numeric)) AS avg_out
FROM generation_traces, jsonb_array_elements(model_calls) AS call
WHERE created_at >= now() - interval '7 days'
GROUP BY 1, 2
ORDER BY spend DESC;
```

Expect the `fast` tier to dominate call *count* and the `behavior`/`health`
tiers to dominate *spend*. If `fast` dominates spend, something is calling the
strong model at a call site meant to be cheap.

---

## 2. Quality

### Grounding rate over time

The corpus-coverage signal. A falling rate means queries are drifting away from
what the corpus covers.

```sql
SELECT date_trunc('day', created_at)::date AS day,
       count(*)                            AS behavior_answers,
       round(100.0 * count(*) FILTER (WHERE answer_mode = 'corpus_grounded')
             / nullif(count(*), 0), 1)     AS grounding_rate_pct
FROM generation_traces
WHERE corner = 'behavior' AND created_at >= now() - interval '30 days'
GROUP BY day ORDER BY day;
```

### Groundedness failures and regeneration rate

These are four distinct outcomes and averaging them loses the signal.
`regenerated` rising means the prompt is drifting from the corpus;
`failed_fell_back` rising means users are silently getting degraded answers.

```sql
SELECT corner, groundedness, count(*),
       round(100.0 * count(*) / sum(count(*)) OVER (PARTITION BY corner), 1) AS pct
FROM generation_traces
WHERE created_at >= now() - interval '7 days'
GROUP BY corner, groundedness
ORDER BY corner, count DESC;
```

### `no_reliable_information` rate for health

**A rise here means retrieval or the corpus is degrading.** It is the health
corner's early warning, because the honest empty state is what the system falls
back to when it cannot ground an answer.

```sql
SELECT date_trunc('day', created_at)::date AS day,
       count(*)                            AS health_answers,
       round(100.0 * count(*) FILTER (WHERE response_kind = 'no_reliable_information')
             / nullif(count(*), 0), 1)     AS no_info_pct
FROM generation_traces
WHERE corner = 'health' AND created_at >= now() - interval '30 days'
GROUP BY day ORDER BY day;
```

### Negative feedback rate with the reason breakdown

This is the query the whole of Part A exists to make possible. The reason tells
you *which* fix; joining to the trace tells you *where*.

```sql
SELECT f.reason,
       count(*)                              AS reports,
       round(avg(t.cost_usd), 6)             AS avg_cost,
       round(100.0 * count(*) FILTER (WHERE t.answer_mode = 'corpus_grounded')
             / nullif(count(*), 0), 1)       AS pct_from_grounded_answers
FROM feedback f
LEFT JOIN generation_traces t ON t.generation_id = f.generation_id
WHERE f.thumb = 'down' AND f.created_at >= now() - interval '30 days'
GROUP BY f.reason ORDER BY reports DESC;
```

How to read it:

| Reason | Points at | First place to look |
|---|---|---|
| `wrong_information` | corpus or retrieval | the trace's retrieved ids — was the right entry even a candidate? |
| `did_not_answer` | retrieval or coverage | context recall; is there an entry for this at all? |
| `not_specific_to_my_cat` | prompt | retrieval was probably fine — check personalization in the prompt |
| `too_cautious` | prompt | check whether the medical advisory is firing when it should not |

### The failing answers themselves

```sql
SELECT f.reason, f.reason_text, t.query, t.answer_mode, t.response_kind,
       t.groundedness, t.generation_id
FROM feedback f
JOIN generation_traces t ON t.generation_id = f.generation_id
WHERE f.thumb = 'down'
ORDER BY f.created_at DESC
LIMIT 20;
```

Then, for any one of them:

```bash
python -m app.ops.traces show <generation-id>
```

That prints the per-stage retrieval with scores, which signals agreed, every
model call, and the groundedness outcome — enough to tell a retrieval failure
from a generation failure without re-running anything.

---

## 3. Safety

### Emergency gate fires by rule

```sql
SELECT rule, count(*) AS fires
FROM generation_traces, jsonb_array_elements_text(red_flag_rules) AS rule
WHERE created_at >= now() - interval '30 days'
GROUP BY rule ORDER BY fires DESC;
```

### Red-flag responses served

```sql
SELECT canned_response_id, count(*)
FROM generation_traces
WHERE red_flag_fired AND created_at >= now() - interval '30 days'
GROUP BY canned_response_id ORDER BY count DESC;
```

### Did any emergency answer call a model? (must be zero)

The deterministic gate is supposed to short-circuit before inference. This is
the audit.

```sql
SELECT generation_id, created_at, red_flag_rules, model_call_count
FROM generation_traces
WHERE red_flag_fired AND model_call_count > 0;
```

**Any row is a safety incident.** Investigate before shipping anything else.

### Did a generated response ship after a groundedness failure? (must be zero)

```sql
SELECT generation_id, created_at, corner, groundedness, response_kind, answer_mode
FROM generation_traces
WHERE groundedness = 'failed_fell_back'
  AND response_kind NOT IN ('no_reliable_information')
  AND response_kind IS NOT NULL;
```

A `failed_fell_back` health answer must always have degraded to
`no_reliable_information`. A row here means a validation failure did not
degrade, which is the one outcome the health corner is built to prevent.

---

## 4. Reliability

### p50 and p95 latency by stage

```sql
SELECT corner,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY (latency->>'retrieval_ms')::numeric))  AS p50_retrieval,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY (latency->>'retrieval_ms')::numeric)) AS p95_retrieval,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY (latency->>'generation_ms')::numeric))  AS p50_generation,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY (latency->>'generation_ms')::numeric)) AS p95_generation,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY (latency->>'validation_ms')::numeric))  AS p50_validation,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY (latency->>'total_ms')::numeric))      AS p95_total
FROM generation_traces
WHERE created_at >= now() - interval '24 hours'
GROUP BY corner;
```

Broken out by stage because they fail differently: retrieval latency is
Voyage/Cohere/Postgres, generation latency is Anthropic, validation latency is
an extra fast-model round trip.

### Schema validation failure rate

```sql
SELECT call->>'model' AS model,
       count(*) AS calls,
       count(*) FILTER (WHERE call->>'validation' = 'failed') AS failed,
       round(100.0 * count(*) FILTER (WHERE call->>'validation' = 'failed')
             / nullif(count(*), 0), 2) AS failure_pct,
       round(avg((call->>'attempts')::numeric), 2) AS avg_attempts
FROM generation_traces, jsonb_array_elements(model_calls) AS call
WHERE created_at >= now() - interval '7 days'
GROUP BY 1 ORDER BY failure_pct DESC;
```

`avg_attempts` creeping above 1.0 means the model is drifting from the schemas
and every drifting call is billed twice.

### Model API errors and spend-cap rejections

These are in the logs rather than the traces, because a rejected call produces
no answer to trace:

```
llm_spend_cap_reached        -- ERROR, cap refused a reservation
llm_spend_approaching_cap    -- WARNING, crossed SPEND_WARNING_RATIO
LLM spend-cap preflight failed closed
```

```bash
railway logs | grep -E "llm_spend_(cap_reached|approaching_cap)"
```

---

## 5. Corpus

### Which entries are retrieved most

```sql
SELECT row->>'entry_id' AS entry_id, count(*) AS retrievals
FROM generation_traces, jsonb_array_elements(retrieval) AS row
WHERE row->>'stage' = 'final_context'
  AND created_at >= now() - interval '30 days'
GROUP BY 1 ORDER BY retrievals DESC LIMIT 20;
```

### Which entries are NEVER retrieved

**A never-retrieved entry is an aliasing problem, not a useless entry.** It is
the single best signal for what to fix in the corpus: the content is there and
real users are not reaching it, which almost always means the entry's aliases
and keywords do not match the words people actually type.

```sql
WITH retrieved AS (
  SELECT DISTINCT row->>'entry_id' AS entry_id
  FROM generation_traces, jsonb_array_elements(retrieval) AS row
  WHERE created_at >= now() - interval '90 days'
)
SELECT c.id, c.topic
FROM corpus_entries c
LEFT JOIN retrieved r ON r.entry_id = c.id
WHERE r.entry_id IS NULL
ORDER BY c.id;
```

> Adjust `corpus_entries` to the actual corpus table name in your schema
> (migrations 001–003). If the corpus is not queryable from SQL, get the same
> answer by diffing the retrieved set above against the ingested ids.

### Candidate-but-never-final entries

Retrieval finds these and reranking or the grounding rule always drops them —
a different problem from never being a candidate at all.

```sql
WITH stages AS (
  SELECT row->>'entry_id' AS entry_id, row->>'stage' AS stage
  FROM generation_traces, jsonb_array_elements(retrieval) AS row
  WHERE created_at >= now() - interval '30 days'
)
SELECT entry_id,
       count(*) FILTER (WHERE stage = 'hybrid_candidates') AS as_candidate,
       count(*) FILTER (WHERE stage = 'final_context')     AS as_context
FROM stages GROUP BY entry_id
HAVING count(*) FILTER (WHERE stage = 'final_context') = 0
   AND count(*) FILTER (WHERE stage = 'hybrid_candidates') > 5
ORDER BY as_candidate DESC;
```

---

## 6. Alerting

Minimum viable set. The first three are cheap to wire from log-based alerts on
Railway; the fourth is a scheduled query.

| # | Alert | Condition | Why it is on this list |
|---|---|---|---|
| 1 | **Spend crossing 80% of cap** | log `llm_spend_approaching_cap` | Alerting on `llm_spend_cap_reached` instead is too late — by then users are being turned away. `SPEND_WARNING_RATIO` exists to give you the earlier signal. |
| 2 | **Emergency-recall test failure in CI** | `evaluation` job fails | This is the one metric that must never move. It is a build gate, not a dashboard. |
| 3 | **Groundedness failures rising sharply** | `failed_fell_back` share > 2x the 7-day baseline | Users are silently getting degraded answers; nothing else surfaces this. |
| 4 | **Any generation shipping after failed validation** | the §3 query returns any row | Should be structurally impossible. A single row means a safety gate is not doing what it claims. |

Scheduled check for 3 and 4 (run hourly):

```sql
SELECT
  (SELECT count(*) FROM generation_traces
    WHERE red_flag_fired AND model_call_count > 0
      AND created_at >= now() - interval '1 hour')          AS emergency_called_model,
  (SELECT count(*) FROM generation_traces
    WHERE groundedness = 'failed_fell_back'
      AND response_kind IS NOT NULL
      AND response_kind <> 'no_reliable_information'
      AND created_at >= now() - interval '1 hour')          AS shipped_after_failure,
  (SELECT round(100.0 * count(*) FILTER (WHERE groundedness = 'failed_fell_back')
                / nullif(count(*), 0), 2)
     FROM generation_traces
    WHERE created_at >= now() - interval '1 hour')          AS failure_pct_last_hour;
```

Non-zero in either of the first two columns is a page, not a ticket.

---

## 7. Should we add a vendor tool?

### Recommendation: **no, not now.** Revisit at either of two specific triggers.

Options considered: LangSmith, Langfuse (cloud or self-hosted), Helicone.

**What they would add over what exists.** A hosted UI with trace search and
filtering; prompt-version management and side-by-side diffing; LLM-as-judge
evaluation runs with human annotation queues; latency and cost dashboards
without writing SQL; session replay across turns.

**Why it is not worth it yet.**

1. **The essentials are already covered.** `generation_traces` records
   per-stage retrieval with scores, signal consensus, per-call model ids and
   token counts, computed cost, per-stage latency, groundedness outcome, and
   the deterministic gate's rule. That is the full set this application needs
   to answer "why was that answer bad?", and it is queryable in SQL today.
2. **A vendor is another data processor to disclose.** Traces contain user
   queries and cat health details. Adding Langfuse or LangSmith means a
   sub-processor entry in the privacy policy, a DPA, a cross-border transfer
   assessment, and a new place user content lives with its own retention and
   breach surface. That cost is not measured in dollars and it does not shrink
   with usage.
3. **The specific gaps are small at this size.** The real gaps are trace search
   UI and LLM-as-judge evals. At current volume, `python -m app.ops.traces
   recent` plus a SQL client covers the first, and the deterministic harness
   covers regression detection for the second.
4. **Instrumenting twice costs more than it looks.** These SDKs want to wrap
   the model client. The client is deliberately thin, fail-closed, and
   spend-capped; wrapping it adds a failure mode to the path that must never
   fail a request — the exact property `write_trace_safely` was written to
   guarantee.

**If we did adopt one, it would be self-hosted Langfuse**, because it is the
only option that keeps user content on infrastructure already covered by the
existing Supabase DPA, which neutralises reason 2. LangSmith is the strongest
product but is cloud-only. Helicone is a proxy, which puts a third party in the
request path for model calls — unacceptable given the spend cap and fail-closed
behavior depend on that path.

**Revisit when either is true:**

- **More than ~5 minutes per investigation is spent writing SQL**, or more than
  one person needs to investigate. That is when a search UI starts paying for
  itself.
- **Prompt iteration becomes frequent.** The moment there is a second prompt
  version live and answers need comparing across versions, side-by-side
  evaluation with human annotation is genuinely hard to build and cheap to buy.

Until then: the self-built traces are the right call, and the money and the
privacy-policy line are better spent elsewhere.

---

## 8. Retention

Traces contain the user's query and the answer served, so they are:

- **exported** with the account (`GET /account/export` → `generation_traces`)
- **deleted** with the account (`generation_traces.cat_id` cascades from `cats`,
  which cascades from `accounts`)
- **pruned** on a window, default **90 days** (`TRACE_RETENTION_DAYS`)

Pruning is not automatic. Run it on a schedule:

```bash
python -m app.ops.traces prune --yes
```

Ninety days is chosen to comfortably outlast a feedback investigation — a
thumbs-down submitted weeks after the conversation still has its trace — while
not becoming an indefinite archive of health questions about people's pets. If
you shorten it, note that feedback rows survive their traces: the rating remains
and the diagnostic context does not, which is the trade being made.

Verify the window is actually being applied:

```sql
SELECT min(created_at) AS oldest_trace, count(*) AS total FROM generation_traces;
```

`oldest_trace` older than `TRACE_RETENTION_DAYS` means the prune job is not
running.
