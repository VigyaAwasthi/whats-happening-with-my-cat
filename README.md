# Cat companion backend

This repository implements the Phase 1 contracts as an explicit Phase 2
backend: strict CSV ingestion, PostgreSQL/pgvector hybrid retrieval, cat-scoped
session and long-term memory, schema-constrained Anthropic calls, deterministic
safety gates, plain-Python orchestration, and FastAPI routes. Development mode
uses deterministic local substitutes and makes zero paid API calls.

The governing rule is: **the model proposes, code disposes**.

## Safety and isolation invariants

- The health corner screens raw text deterministically before any model call.
  Emergency and urgent rules return pre-written responses and stop generation.
- Medical facts can come only from `VetKnowledgeRetriever`. No match produces an
  explicit no-reliable-information response.
- Health claims must cite retrieved entry IDs. A deterministic citation gate and
  structured groundedness judge run before output. Failed drafts get one retry,
  then unsupported claims are stripped or the response is refused.
- Behavior chat hard-stops only when the deterministic raw-text emergency matcher
  fires. The fuzzy health-signal classifier is advisory: it may append a specific
  corpus-backed health note to a complete answer, but cannot replace that answer.
- Every model output used by control flow is validated as a Pydantic model. There
  is no string parsing of model prose.
- Repository methods require `cat_id`. Session context is filtered by cat,
  session, and corner. A cat or corner switch creates a soft session reset and
  the chat response returns the effective replacement session ID.
- Moments are scrapbook-only data and no memory or retrieval module references
  their table.
- `require_active_cat` protects only cat-scoped endpoints. Zero-cat onboarding,
  cat creation, export, and account deletion remain account-scoped.

## Model call map

The order below is the code path, not a conceptual diagram. “Fast” and “strong”
mean the configured model IDs; deterministic and retrieval steps do not call
Anthropic.

### Health corner

| Step | Model? |
| --- | --- |
| Raw-text two-tier safety screen | No |
| Structured red-flag rules, when intake is supplied | No |
| `SymptomIntake` extraction, only when intake is absent | Fast |
| Structured red-flag re-check after extraction | No |
| Hybrid retrieval and reranking | Embedding/reranker only; no generative model |
| `TriageResult` proposal | Strong health model |
| Citation-existence validation | No |
| Groundedness judge | Fast |
| One bounded regeneration after failed groundedness | Strong health model |
| Session compaction, only when needed | Fast |

An emergency is decided entirely by code and short-circuits before extraction,
retrieval, or answer generation. Its memory write explicitly disables session
compaction, so even an overlong session cannot cause a model call on the
emergency path.

### Behavior corner

| Step | Model? |
| --- | --- |
| Deterministic raw red-flag screen | No |
| `HealthSignalCheck` | Fast |
| Hybrid behavior retrieval | Embedding/reranker only |
| Answer-mode evidence comparison | No |
| `BehaviorInterpretation` proposal | Strong behavior model |
| Light groundedness, `corpus_grounded` only | Fast |
| Session compaction, only when needed | Fast |

Only the deterministic red-flag screen can redirect. The health-signal classifier
always runs as an advisory proposal; where a selected corpus entry contains a
specific `medical_flag`, sufficient classifier confidence or change language may
append that exact concern and a warm health-corner offer to the completed answer.
Corpus `medical_flag` text is never an input to hard routing or mode selection.

## Behavior grounding modes

- `corpus_grounded` is selected only when the reranker winner also wins the
  semantic channel and either wins the lexical channel or covers enough meaningful
  query concepts in curated parent text. Code resolves its readable source title,
  organization, and optional URL, and only corpus clarifying questions survive.
- `general_knowledge` is used when no parent has that evidence. It is
  personalized from the active cat profile, capped at `varies-by-cat`, and is
  structurally forbidden from carrying citations. General questions are allowed
  only in this mode.

Mode selection is deterministic code, never a model judgment, and deliberately
ignores absolute cross-encoder magnitudes. The local
`cross-encoder/ms-marco-MiniLM-L-6-v2` returns raw logits; the adapter's sigmoid
turns valid paraphrases such as “kneads and drools on wool” into `0.0059`.
Consequently, behavior retrieval preserves reranked candidates regardless of
score. The configurable evidence settings are
`BEHAVIOR_GROUNDING_MIN_QUERY_COVERAGE` (default `0.65`) and
`BEHAVIOR_GROUNDING_MIN_QUERY_TERMS` (default `2`).

Against the 47-entry corpus, the selected composite grounded all 15 labeled
positives, grounded 3 of 15 deliberately marginal queries, and left 21 of 25
quirky queries in general-knowledge mode. The four quirky questions now backed by
real entries were hair ties (`bringing-gifts`), the bathroom sink
(`sitting-in-boxes`), bird chatter (`chattering-at-birds`), and prolonged wall
staring (`staring`). The mirror query remains general knowledge. Pure reranker
margin was rejected: Cohere gave the unsupported mirror match a larger top-two
gap (`0.3442`) than the valid kneading paraphrase (`0.0232`). Strict lexical and
semantic consensus was also rejected because PostgreSQL full-text search returned
no candidate for the valid kneading wording. Re-run this evidence calibration
whenever the corpus, embedding model, lexical query, or reranker changes.

With the local model already cached, the reproducible calibration command is:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
.venv/bin/python -m tests.routing.calibrate_threshold
```

The frontend renders `general_knowledge` with the quiet “General feline
understanding” provenance line in place of a source block.

## Retrieval and corpus ingestion

The source CSV directory is configurable with `CORPUS_SOURCE_DIR` and defaults
to:

```text
/Users/vigyaawasthi/Documents/Whats happening with my cat/
```

Health and behavior rows are parents. Semantic child chunks are created on
meaningful field boundaries, enriched with parent aliases and keywords, and
embedded in batches. Hybrid retrieval runs pgvector cosine and PostgreSQL
full-text search concurrently, fuses rankings with reciprocal rank fusion,
expands children to complete parents, and applies a selectable cross-encoder
reranker.

Fun facts are flat, curated, tag-selected records. `detail` is required and
stored verbatim. Ingestion drops redundant `general` tags and retains
`all-cats`; facts are never chunked or embedded.

All non-empty corpus source links must be absolute HTTP(S) URLs without
whitespace, bracketed verification markers, or placeholder text. Ingestion fails
loudly on malformed links; nullable links retain their title and organization so
clients render a plain-text citation.

Embeddings use the configured model (default `voyage-3`) at 1024 dimensions.
Changing dimensions requires a migration and a complete re-embed.

Production requires positive regular-input, output, cache-write, and cache-read
token prices for each configured model tier. Before a Messages API call, the
client obtains an exact input-token count and atomically reserves the worst-case
cost of both bounded attempts in PostgreSQL. Reconciliation uses the response's
separate billing categories. The cumulative ledger therefore survives restarts
and coordinates multiple application workers.

## Local setup

Python 3.11 or later and PostgreSQL with pgvector are required.

```bash
python -m venv .venv
.venv/bin/pip install -e '.[test,local-reranker]'
cp .env.example .env
```

Apply migrations in order:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/001_initial.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/002_seed_corpus.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/003_phase2_resolutions.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/004_persistent_spend_ledger.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/005_cat_media_storage.sql
```

The initial migration uses Supabase `auth.uid()` when present and creates a
compatible local shim only when it is absent.

Run zero-cost deterministic ingestion:

```bash
RUNTIME_MODE=development \
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/cat_companion \
.venv/bin/python -m app.ingestion.cli
```

Run the API without external services:

```bash
RUNTIME_MODE=development .venv/bin/uvicorn app.main:app --reload
```

Browser clients must use an exact origin listed in `CORS_ALLOWED_ORIGINS`.
Development defaults allow `http://localhost:3000` and
`http://localhost:5173`; add the deployed UI origin before production.

Run against Supabase and the configured model providers only after the provider
accounts have usable credit and rate limits:

```bash
RUNTIME_MODE=production \
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Production startup validates the full price table, model split, embedding
dimension, and spend cap. The local cross-encoder is lazy-loaded on its first
retrieval request, so install the `local-reranker` extra and warm it for zero-cost
development only. Its magnitudes are uncalibrated and it misranked one of the 15
marginal audit cases; production should use the empirically verified hosted
Cohere `rerank-v3.5` adapter.

## UI integration contract

- OpenAPI is available from the running backend at `/openapi.json`; interactive
  documentation is at `/docs`.
- Supabase access tokens are sent as `Authorization: Bearer <token>`.
- Every cat-scoped request sends `X-Active-Cat-ID` and the same `cat_id` in its
  body or query string.
- Both chat responses return the effective `session_id`. The UI must replace its
  local session ID with this value after every response so cat/corner switches
  retain the server-created session.
- Health responses branch on `result.response_kind`: `triage`,
  `emergency_canned`, or `no_reliable_information`. Do not infer this state from
  the prose message.
- An account with no cats is valid. Cat-scoped calls return the typed
  `NO_ACTIVE_CAT` error until onboarding creates or selects a cat.
- Profile and moment media lives in the private `cat-media` Supabase Storage
  bucket. Upload object names as `<cat_id>/<unique-object-name>` and persist that
  complete object name as `media_key` or in `photo_references`. Storage RLS
  authorizes the cat owner; the backend never retrieves moment media into AI
  context.

Create the first cat without an active-cat header, then include both the
`X-Active-Cat-ID` header and matching `cat_id` on cat-scoped requests:

```bash
curl -X POST http://127.0.0.1:8000/chat/health \
  -H 'Content-Type: application/json' \
  -H 'X-Active-Cat-ID: cccccccc-cccc-cccc-cccc-cccccccccccc' \
  -d '{
    "cat_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
    "message": "My cat cannot pee",
    "intake": null,
    "session_id": "11111111-1111-1111-1111-111111111111"
  }'
```

## Verification

```bash
.venv/bin/python -B -m pytest -p no:cacheprovider -q
.venv/bin/mypy app --no-incremental
```

The suite covers contract validation, ingestion and migration invariants, API
onboarding, all ten required safety acceptance cases, cross-cat isolation,
session reset, malformed structured output, and the prohibition on moments
retrieval.

The routing regression suite lives in `tests/routing/`: 195 emergency/urgent
paraphrases, 45 normal-behavior negatives, 12 recorded boundary inputs, and 25
quirky-behavior cases. `tests/evaluation/golden_health.json` adds 18
reviewable retrieval/response cases. The zero-cost evaluator reports
RAGAS-style faithfulness, answer relevance, context precision, context recall,
and sentence-level groundedness in CI.

Those metrics can detect retrieval drift and an answer that is unfaithful to
the supplied context. They cannot detect a factually wrong curated corpus;
medical corpus review remains a separate human responsibility.

## Feedback traceability

The frontend currently calls `POST /feedback` from both chat corners. The
payload and database row identify the cat, session, and corner, but not the
specific generated response or its retrieval set. Feedback on an older message
can therefore be associated only with the session, not the generation that
produced it. This is a Phase 4 blocker: add a server-issued generation ID to
chat responses, persist the corresponding retrieved IDs, and send that ID from
the feedback button. It is deliberately not guessed from “latest response,”
which would be wrong under concurrent or delayed feedback.

## Open questions

- `SymptomIntake` lacks explicit fields for several coded red flags. The raw
  deterministic pre-screen covers them; `free_text_residual` is never parsed.

The curated health corpus now contains 35 unique rows. Supabase email
confirmation is disabled for development because the current sign-up contract
returns session tokens immediately.
