# Deployment

Backend: FastAPI on **Railway**. Frontend: Next.js on **Cloudflare Workers**.
Database, auth, and storage: **Supabase**. Models: **Anthropic** (chat and
health), **Voyage** (embeddings), **Cohere** (rerank).

> **Divergence from the original target architecture.** The brief specified
> Next.js on Vercel. The frontend in this repository is built for Cloudflare
> Workers — `vinext`, `wrangler`, and `@cloudflare/vite-plugin`, with a Worker
> entrypoint at `frontend/worker/index.ts` and a `wrangler.json` generated into
> `dist/server/` at build time. Moving to Vercel would mean removing the
> Cloudflare adapter and rewriting the Worker entrypoint and its tests. This
> document deploys the stack that exists. Nothing else in the checklist depends
> on which host serves the frontend, apart from the exact origin string that
> goes into `CORS_ALLOWED_ORIGINS`.

---

## Table of contents

1. [Model pinning policy](#1-model-pinning-policy)
2. [Pricing](#2-pricing)
3. [The spend cap](#3-the-spend-cap)
4. [Environment separation and secrets](#4-environment-separation-and-secrets)
5. [Supabase configuration](#5-supabase-configuration)
6. [Deploying the backend to Railway](#6-deploying-the-backend-to-railway)
7. [Deploying the frontend to Cloudflare Workers](#7-deploying-the-frontend-to-cloudflare-workers)
8. [Rollback](#8-rollback)
9. [Observability](#9-observability)
10. [Post-deploy verification (Part C)](#10-post-deploy-verification-part-c)
11. [Pre-deploy checklist](#11-pre-deploy-checklist)

---

## 1. Model pinning policy

**Upgrading a model is a deliberate change. It requires a source edit, a re-run
of the routing suite, and a re-run of the evaluation suite. It is never an
environment-variable change against a running deployment.**

### Why the mechanism is an allowlist and not a date suffix

The usual way to pin a model is to replace a floating alias with a dated
snapshot: `claude-3-5-sonnet` becomes `claude-3-5-sonnet-20241022`. That does
not apply to the models this service uses.

`claude-sonnet-5` **is** the complete, canonical identifier. The Claude 5 family
does not publish dated snapshot variants, and appending a date — for example
`claude-sonnet-5-20260115` — resolves to no model at all and returns a 404. So
there is no dated string to migrate to, and "pinning" has to be expressed some
other way.

The mechanism is `REVIEWED_ANTHROPIC_MODELS` in
[`app/runtime_config.py`](app/runtime_config.py):

```python
REVIEWED_ANTHROPIC_MODELS: frozenset[str] = frozenset({
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
})
```

Under `RUNTIME_MODE=production`, startup **fails** if `ANTHROPIC_FAST_MODEL`,
`ANTHROPIC_BEHAVIOR_MODEL`, or `ANTHROPIC_HEALTH_MODEL` names anything outside
that set. Changing the model therefore requires editing tracked source, which
means a commit, a review, and a CI run — the properties date-pinning was meant
to buy.

The fast tier keeps its dated identifier (`claude-haiku-4-5-20251001`) because
Haiku 4.5 does publish one. Both forms sit in the same allowlist.

### Procedure for changing a model

1. Add the new identifier to `REVIEWED_ANTHROPIC_MODELS`.
2. Add its four price rates to the environment (see [§2](#2-pricing)). The
   settings validator rejects two tiers that name the same model with different
   rates.
3. Re-run the routing suite: `pytest tests/routing`. Mode selection is
   threshold-based and calibrated against a specific model's outputs.
4. Re-run the evaluation suite: `pytest tests/test_evaluation.py`, and review
   the metric deltas rather than only the pass/fail.
5. Re-run the full safety set: `pytest`, then the 195 emergency paraphrases, the
   45 behavior negatives, and the 25 quirky behavior cases.
6. Deploy backend and frontend together, then run
   [§10](#10-post-deploy-verification-part-c).

Do **not** skip steps 3 and 4 for a model that is "obviously better". The
routing thresholds and the groundedness validator are tuned to observed model
behavior; a stronger model can change mode selection and shift the
health/behavior boundary.

---

## 2. Pricing

Production refuses to start until every rate is positive, because the spend cap
is computed from these numbers. All twelve rates live in the Railway dashboard.

| Tier | Model | Input | Output | Cache write | Cache read |
|---|---|---:|---:|---:|---:|
| fast | `claude-haiku-4-5-20251001` | 1.00 | 5.00 | 1.25 | 0.10 |
| behavior | `claude-sonnet-5` | 3.00 | 15.00 | 3.75 | 0.30 |
| health | `claude-sonnet-5` | 3.00 | 15.00 | 3.75 | 0.30 |

USD per million tokens. Cache rates follow the published multipliers on each
model's input rate: cache write (5-minute TTL) is 1.25x input, cache read is
0.10x input.

### Sonnet 5 introductory pricing ends 31 August 2026

Sonnet 5 currently bills at introductory rates of **$2.00 / $10.00** per million
tokens. Those rates end **31 August 2026**, after which it bills at the standard
**$3.00 / $15.00**.

**The table above configures the standard rates, not the introductory ones.**
That is deliberate. Configuring the introductory rates would make the ledger
under-count every call from 1 September onward — silently, because nothing in
the system can detect a price change — and the cap would stop being a cap at
exactly the moment spend rose by 50%. Configuring the standard rates makes the
ledger conservative during the introductory period (it over-counts by a third,
so the cap binds early and safely) and correct afterwards, with no dated action
required.

If you would rather track actual spend precisely during the introductory
period, set the behavior and health input/output rates to `2.00`/`10.00` and
cache rates to `2.50`/`0.20` — and put a calendar reminder on **31 August 2026**
to change all eight values back. Confirm current rates in the Anthropic console
before either choice; the numbers above are from the published pricing table and
the console is authoritative.

---

## 3. The spend cap

### What changed

Migration `004` created a single cumulative row keyed `global`. Once spend
reached the cap, every model call failed and stayed failing until someone
manually edited a database table. There was no window and no reset.

The ledger is now **windowed**, and the window is encoded in the key itself:

| `SPEND_WINDOW` | Ledger key | Reset boundary |
|---|---|---|
| `monthly` (default) | `global:YYYY-MM` | 00:00 UTC on the 1st of each month |
| `lifetime` | `global` | Never — the original behavior |

Nothing runs at the boundary. No cron, no migration, no operator action: a new
month simply accumulates against a new row. A cap reached in July stops blocking
calls the instant August's key comes into use. Old rows are retained as spend
history.

`SpendReservation` carries the key that was actually debited, so a call that
straddles the month boundary reconciles against the row it charged rather than
the new month's empty row.

### Confirm the migrations are applied

Both `004` and `008` must be applied to the **production** Supabase project.
`008` is additive and idempotent — an index and two comments — and does not
rewrite any existing row.

```bash
psql "$DATABASE_URL" -c "\d llm_spend_totals"
```

```bash
psql "$DATABASE_URL" -c "SELECT budget_key, spent_usd, updated_at FROM llm_spend_totals ORDER BY budget_key DESC;"
```

You should see the `llm_spend_totals` table and the
`llm_spend_totals_updated_at_idx` index. If the table is missing, apply
`db/migrations/004_persistent_spend_ledger.sql` and then
`db/migrations/008_windowed_spend_ledger.sql`, in that order, **after taking a
backup**.

### Inspecting and resetting spend

```bash
python -m app.ops.spend show
```

```bash
python -m app.ops.spend show --all-windows
```

```bash
python -m app.ops.spend reset --window 2026-07 --yes
```

Run it with the production `DATABASE_URL` in the environment; it reads exactly
what the API reads. `reset` prompts for the window key unless `--yes` is given.

### Approaching-cap warning

`SpendTracker` logs `llm_spend_approaching_cap` at **WARNING** once per process
per window when spend crosses `SPEND_WARNING_RATIO` (default `0.8`), and
`llm_spend_cap_reached` at **ERROR** when a reservation is refused. Alert on the
warning, not the error — by the time the error fires, users are already being
turned away.

---

## 4. Environment separation and secrets

### Distinct Supabase projects

Development and production use **separate Supabase projects**, not separate
schemas in one project. Separate projects give separate auth user pools,
separate storage buckets, and separate service-role keys, so a development key
cannot reach production data even by accident. Separate schemas share all three.

Each project needs migrations `001` through `008` applied and its own corpus
ingestion run.

### No secret in the repository

```bash
./scripts/scan_secrets.sh --history
```

This passes on the current repository, for both the working tree and every blob
in git history. The scan runs in CI on every push
(`.github/workflows/backend-ci.yml`), covering provider key formats, JWTs,
private keys, and DSNs with embedded passwords, with an explicit allowlist for
documented placeholders.

`.env` is git-ignored and has never been tracked. `.env.example` holds empty
values and placeholders only, and the scan asserts that.

**If the scan ever finds something, rotate the credential in the provider
console first.** Deleting the line does not un-leak it; the value is in the
history and in every clone.

### Where secrets live

| Secret | Set in |
|---|---|
| `DATABASE_URL`, `SUPABASE_*`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `RERANKER_API_KEY` | Railway dashboard → service → Variables |
| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_BASE_URL` | Cloudflare build environment (see [§7](#7-deploying-the-frontend-to-cloudflare-workers)) |

The `NEXT_PUBLIC_*` values are compiled into the client bundle and are public by
design. The Supabase **anon** key belongs there; the **service-role** key never
does — it is server-only and lives on Railway alone.

### The startup check

Under `RUNTIME_MODE=production`, the process refuses to start when any of these
is true, and says which:

- a required secret is empty;
- a required value still contains a placeholder marker (`YOUR_`, `CHANGEME`,
  `development-unused`, and similar);
- a `CORS_ALLOWED_ORIGINS` entry is not a bare `scheme://host[:port]`;
- an Anthropic model id is not on the reviewed allowlist;
- any of the twelve price rates is zero or negative;
- `RERANKER_MODE=local` — the development cross-encoder, whose
  sigmoid-normalized logits are not calibrated relevance probabilities.

The failure is loud and immediate: the container exits, the Railway health check
never passes, and the previous deployment keeps serving.

---

## 5. Supabase configuration

### Enable email confirmation

Dashboard → **Authentication → Providers → Email** → enable *Confirm email*.
Then under **Authentication → URL Configuration**, add the confirmation return
URL to **Redirect URLs**:

```
https://<your-frontend-domain>/auth/confirmed
```

Set the same value as `SUPABASE_EMAIL_REDIRECT_URL` on Railway. If the URL is
not registered, Supabase silently substitutes the project's Site URL and the
user lands somewhere unexpected.

### The `AuthSessionResponse` contract implication

With confirmation enabled, `POST /auth/sign-up` **does not return session
tokens**. The identity exists and is unusable until the emailed link is
followed.

The previous contract could not express that: `access_token`, `refresh_token`,
and `expires_in_seconds` were all required on every response, so the only way to
satisfy it was to leave email confirmation switched off. The contract is now a
discriminated union:

```jsonc
// sign-up, confirmation enabled
{ "status": "confirmation_required",
  "access_token": null, "refresh_token": null, "expires_in_seconds": null }

// sign-in after confirming
{ "status": "active",
  "access_token": "...", "refresh_token": "...", "expires_in_seconds": 3600 }
```

A validator guarantees the three token fields are present together or absent
together, so no caller can receive a half-populated session. **Clients must
branch on `status` before reading `access_token`.**

Signing in before confirming now returns a typed `403 EMAIL_NOT_CONFIRMED`
rather than the opaque 500 it produced before, so the frontend can tell the user
the one thing that fixes it.

The implemented flow is: sign up → "check your email" interstitial → user clicks
the link → lands on `/auth/confirmed` → returns to sign in → active session.
Both the sign-up response and a `403 EMAIL_NOT_CONFIRMED` on sign-in route to
the same interstitial.

### Storage

Migrations `005` and `006` create the `cat-media` bucket and its row-level
policies. Media uploads go directly from the browser to Supabase Storage using
the user's own session, so the backend never proxies image bytes.

### Voyage rate limits

A Voyage account **without a payment method is limited to 3 requests per
minute**, which fails under any real traffic. Add a payment method before
launch.

The embedding client already degrades correctly rather than failing a user's
query, and this behavior should be preserved: `VoyageEmbeddingProvider` honors
the `Retry-After` header, falls back to pacing at 20.5s when the header is
absent (the reduced-tier interval), retries four times with exponential backoff,
serializes requests behind a lock so retries do not stampede, and finally
returns null vectors rather than raising. A retrieval path that receives null
vectors returns "no reliable information" — the honest empty state — instead of
an error page.

---

## 6. Deploying the backend to Railway

### One-time setup

1. Create a Railway project and add a service from this GitHub repository.
2. Set the **root directory** to the repository root.
3. Railway reads [`railway.json`](railway.json) for build and deploy settings:
   build with `pip install -r requirements.txt`, start with uvicorn, health
   check `/health`, restart on failure up to 3 times.
4. Set every variable from [`.env.example`](.env.example) in **Variables**, with
   `RUNTIME_MODE=production` and `LOG_FORMAT=json`. Do not set
   `CORPUS_SOURCE_DIR`; the deployed API never reads the CSVs.
5. Generate a public domain under **Settings → Networking**.

### What is pinned

| Concern | Mechanism |
|---|---|
| Python version | [`.python-version`](.python-version) → `3.12` |
| Dependencies | [`requirements.txt`](requirements.txt), fully pinned including transitive |
| Start command | [`Procfile`](Procfile) and `railway.json` |

`requirements.txt` is the deployment lockfile: two deploys of the same commit
install byte-identical dependencies. `pyproject.toml` declares the intended
ranges; regenerate the lockfile after changing it (the header explains how) and
re-run `pytest` and `mypy` against the new set. CI installs from the lockfile
and imports the app, so drift is caught before Railway sees it.

The `local-reranker` extra (sentence-transformers, torch, transformers) is
deliberately excluded — production uses the hosted reranker, and leaving it out
removes roughly a gigabyte of wheels the service never imports.

### `numReplicas` is 1, deliberately

The per-account chat rate limiter counts in process memory. With N replicas the
effective ceiling is N times `CHAT_RATE_LIMIT_PER_MINUTE`. That is acceptable —
the limiter is cost protection, and the hard spend cap in the shared PostgreSQL
ledger is the real backstop — but if you scale up, either accept the looser
per-account ceiling or move the limiter to shared storage first.

### Deploy

Push to the tracked branch. Railway builds, starts the process, and polls
`/health` before shifting traffic. A deployment whose configuration is rejected
by the startup check never passes the health check, so the previous deployment
keeps serving.

---

## 7. Deploying the frontend to Cloudflare Workers

### Build-time environment variables

`NEXT_PUBLIC_*` values are **inlined into the bundle at build time**, not read at
runtime. They must be present in the environment that runs `npm run build`.
Setting them as Worker runtime variables afterwards has no effect — this is the
single most common way this deploy goes wrong.

```dotenv
NEXT_PUBLIC_API_BASE_URL=https://<your-railway-domain>
NEXT_PUBLIC_SUPABASE_URL=https://<prod-project-ref>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<prod anon key>
```

The backend URL is already an environment variable rather than a hardcoded value
([`frontend/lib/api.ts`](frontend/lib/api.ts)); it falls back to
`http://127.0.0.1:8000` only when unset, which is what you want locally and
never want in production. **Verify the built bundle contains the production
URL** before deploying — see the check in
[§10](#10-post-deploy-verification-part-c).

### Build and deploy

```bash
cd frontend && npm ci && npm run build
```

```bash
cd frontend && npx wrangler deploy -c dist/server/wrangler.json
```

The generated `wrangler.json` names the Worker `whisker-rooms-frontend` and
serves static assets from `dist/client`. `npm run build` also runs as part of
`npm test`, so the safety-contract tests always run against the production
bundle.

### After the first deploy

Add the Worker's origin to `CORS_ALLOWED_ORIGINS` on Railway and redeploy the
backend. Origins are scheme, host, and port only — **no trailing slash, no
path**. The startup check rejects anything else, so a malformed entry fails the
deploy rather than silently breaking the browser at runtime.

```dotenv
CORS_ALLOWED_ORIGINS=["https://whisker-rooms-frontend.<subdomain>.workers.dev","http://localhost:3000"]
```

`http://localhost:3000` stays for development.

---

## 8. Rollback

**Nothing deploys that cannot be rolled back.** Both platforms keep prior
immutable versions; the database is the one component where rollback is a
restore, not a redeploy.

### Backend (Railway)

Dashboard → service → **Deployments** → pick the last known-good deployment →
**Redeploy**. Or:

```bash
railway redeploy <deployment-id>
```

Because dependencies come from a pinned lockfile and the Python version is
pinned, redeploying an old commit rebuilds the same artifact rather than picking
up whatever has been published since.

**Rolling back an environment variable is a separate action.** Railway variable
changes trigger a new deployment but are not part of the deployment artifact, so
redeploying an old build does *not* restore old variables. Record variable
changes alongside the deploy that needed them.

### Frontend (Cloudflare Workers)

```bash
cd frontend && npx wrangler deployments list
```

```bash
cd frontend && npx wrangler rollback <deployment-id>
```

Rollback is near-instant and does not rebuild.

### Database

Migrations are forward-only. Rollback is a restore from backup, so **take a
backup before applying any migration** (Supabase dashboard → Database → Backups,
or `pg_dump`).

`008` is the only migration in this change set and it is additive and idempotent
— an index and two comments. Rolling back the application while `008` is applied
is safe: the older code reads and writes the `global` key, which `008` does not
touch. Rolling *forward* is likewise safe, since `SPEND_WINDOW` defaults to
`monthly` and simply starts a new key.

### Order of operations

- **Deploying:** database migration → backend → frontend.
- **Rolling back:** frontend → backend → database (only if truly required).

Roll the frontend back first: an old frontend against a new backend is the
compatible direction, since the backend's `AuthSessionResponse` change is
additive (`status` has a default) while the frontend's handling of it is not.

---

## 9. Observability

### Logging

`LOG_FORMAT=json` emits one JSON object per line — timestamp, level, logger,
message, and a redacted exception field when present. `LOG_LEVEL=INFO` is right
for production; `DEBUG` is not, because third-party libraries log request
details at that level.

**No application log statement records user messages, model outputs, prompts, or
secrets.** This was audited across every `logger.*` call site and is pinned by
tests in
[`tests/test_deployment_readiness.py`](tests/test_deployment_readiness.py). What
is logged is identifiers (cat and session UUIDs), outcomes, and counts.

As defence in depth for code we do not own — a psycopg error quoting the DSN, an
httpx error quoting a signed URL — a redacting filter runs at the log handler and
masks DSN passwords, `sk-ant-`/`pa-`/`sb_` keys, JWTs, and
`Authorization`/`Bearer` values. Tests assert each pattern.

Uvicorn's access log is disabled: it duplicates what the platform already records
and its paths contain cat and moment identifiers.

**Stack traces go to logs and never to HTTP responses.** Every handler returns
the typed `APIErrorResponse` body, including framework-raised 404/405 and
validation failures. The validation handler in particular does not echo the
rejected value, because on the chat routes that value is the user's message.

### Keep the model-call log

`llm_call` in [`app/llm/client.py`](app/llm/client.py) records model, purpose,
latency, input/output tokens, cache reads, cache creations, validation outcome,
and attempt number. It is the only visibility into cost and reliability — it is
how you find out that cache hits collapsed or that validation retries doubled. Do
not remove it or lower it below INFO.

### What to alert on

| Signal | Why |
|---|---|
| `llm_spend_approaching_cap` (WARNING) | Users are still being served; you have time to act |
| `llm_spend_cap_reached` (ERROR) | Already turning users away |
| `/ready` returning 503 | Database unreachable or configuration fault |
| `validation=failed` rate in `llm_call` | Model output drifting from the schemas |
| Sustained 429s from the chat endpoints | One account hammering, or the limit is too low |

---

## 10. Post-deploy verification (Part C)

Exercise the **deployed URLs**. A local pass proves nothing about production
configuration, which is where these failures live.

### Automated

```bash
python scripts/verify_deployment.py --api https://<railway-domain> --origin https://<worker-domain> --email you+verify@example.com --password '<throwaway password>'
```

The script refuses to run against `localhost`. It creates a real account and real
cats in the target project and deletes the account at the end, so use a throwaway
address. It covers: both probes and their non-leakage; typed errors with no stack
trace on the unauthenticated surface; CORS accept and reject; the sign-up
confirmation-pending contract; behavior answer with a mode indicator; the
deterministic emergency gate with no model call; the `no_reliable_information`
empty state; cat-scope mismatch and foreign-cat refusal; rate limiting; export;
and the account-deletion cascade.

Because email confirmation is enabled, the first run stops after sign-up. Click
the emailed link, then re-run to exercise the authenticated checks.

### Manual — needs a browser or an inbox

| # | Check | How |
|---|---|---|
| 1 | Sign up through the production frontend, including the confirmation email | Full flow in a real browser; confirm the interstitial appears and the link lands on `/auth/confirmed` |
| 2 | Create a cat with photos; confirm they persist | Upload through the UI, then check Supabase → Storage → `cat-media` for the objects, and reload the page to confirm the signed URLs resolve |
| 6 | Create a second cat, switch, confirm isolation holds | Both corner histories must clear and a new `session_id` must be issued on switch |
| 8 | No console errors in the browser | DevTools console and network tab through the whole flow; CORS failures appear here and nowhere else |
| 9 | The spend ledger recorded the calls with correct costs | `python -m app.ops.spend show` against the production `DATABASE_URL` |

For item 9, sanity-check the arithmetic rather than only that a number appeared:
a handful of behavior and health queries at Sonnet 5 rates should land in the low
cents. A total of exactly zero after real model calls means the ledger is not
recording; a total far above expectation means a price rate has an extra digit.

### Verify the frontend bundle points at production

```bash
grep -ro "https://[a-z0-9.-]*railway.app" frontend/dist/ | head
```

An empty result means `NEXT_PUBLIC_API_BASE_URL` was not set at build time and
the bundle is still pointing at `http://127.0.0.1:8000`.

### Report production-only differences

Anything that behaves differently in production than locally is almost always
configuration. Record it. The likely candidates: a CORS origin that does not
match exactly; `NEXT_PUBLIC_*` not present at build time; email confirmation
changing the sign-up flow; the hosted reranker returning different scores than
the local cross-encoder, which shifts retrieval and therefore mode selection; and
Voyage rate limits under concurrency.

---

## 11. Pre-deploy checklist

**Safety invariants — these must behave identically in production. Do not weaken
any of them for deployment convenience.**

- [ ] `pytest` and `mypy app` both clean
- [ ] 195 emergency/urgent paraphrases pass with zero failures
- [ ] 45 behavior negatives and 25 quirky behavior cases pass
- [ ] The 18-case golden retrieval/answer dataset run and metric deltas reviewed
- [ ] `unusual breathing` is coded emergency with **no model call**
- [ ] `why does my cat sleep with me at night?` receives a behavior answer
- [ ] A health no-match returns `no_reliable_information`; general-knowledge mode
      is never enabled in health
- [ ] Cat isolation holds across switch, chat, memory, and moments

**Configuration**

- [ ] `./scripts/scan_secrets.sh --history` passes
- [ ] Every model id is on `REVIEWED_ANTHROPIC_MODELS`
- [ ] All twelve price rates positive and matching [§2](#2-pricing)
- [ ] `RUNTIME_MODE=production`, `LOG_FORMAT=json`, `LOG_LEVEL=INFO`
- [ ] `RERANKER_MODE=hosted` with the Cohere key and `rerank-v3.5`
- [ ] `CORS_ALLOWED_ORIGINS` contains the exact production origin, no trailing slash
- [ ] `SUPABASE_EMAIL_REDIRECT_URL` set and registered in Supabase
- [ ] Distinct Supabase projects for dev and prod

**Data**

- [ ] Production database backed up
- [ ] Migrations `001`–`008` applied and recorded
- [ ] `004` and `008` confirmed present (`\d llm_spend_totals`)
- [ ] Corpus ingested idempotently: 35 health, 47 behavior, 34 fun-fact parents
- [ ] Vector column is 1024 dimensions
- [ ] Voyage payment method added

**After deploying**

- [ ] `scripts/verify_deployment.py` passes against the deployed URLs
- [ ] Manual checks 1, 2, 6, 8, 9 from [§10](#10-post-deploy-verification-part-c)
- [ ] Rollback rehearsed at least once on the frontend

---

## Known gap: feedback attribution

`POST /feedback` accepts only cat, session, corner, and thumb. That cannot
attribute feedback to one retrieval set and generation, especially when feedback
is delayed or several exchanges share a session. Closing it needs a
server-issued generation ID on each chat response, persisted with the exact
retrieved entry IDs and model metadata, submitted back by the frontend, and
rejected or clearly marked when absent.

This does not block deployment — it means feedback data collected before it is
fixed cannot be used to attribute quality to specific retrievals.
