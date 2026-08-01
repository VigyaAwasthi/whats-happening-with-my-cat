# Whisker rooms frontend

The Phase 3 web client for the cat companion API. It includes Supabase
authentication and media storage, zero-cat onboarding, the photo-wall hub, and
the behavior, health, facts, and moments corners.

## Run locally

Requires Node.js 22.13 or newer and the backend on port 8000.

```bash
cp .env.example .env.local
npm install
npm run dev
```

The frontend opens at `http://localhost:3000`. Configure:

```dotenv
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
```

All cat-scoped calls send both `cat_id` and `X-Active-Cat-ID`. The returned
`session_id` is authoritative and replaces the local session after every chat
response.

## Quality gates

```bash
npm run lint
npm exec tsc -- --noEmit --incremental false
npm test
npm audit --omit=dev
```

`npm test` builds the production worker and verifies the frontend safety
contracts: emergency inversion, citation fail-closed behavior, active-cat
scoping, session resets, exact clarifying questions, moments isolation,
keyboard bubbles, reduced motion, typography, and social metadata.

## Safety and privacy

- Health triage renders only when every claim cites a retrieved entry.
- A failed client citation check degrades to a veterinarian referral.
- Canned emergencies use the near-black `--ink` block with cream text.
- The health emergency boundary is permanent and non-dismissible.
- `NO_ACTIVE_CAT` from any cat-scoped endpoint routes back to onboarding.
- Cat switching clears both corner histories and creates new session IDs.
- Moments only calls scrapbook and storage APIs; it is never sent to AI paths.

## Design system

Newsreader is the warm editorial display serif; Manrope is the UI and body
sans. Both are loaded through `next/font`. The contrast between a print-like
serif and a compact, highly legible sans supports the “quiet canvas, lively
behavior” direction without making the static layout whimsical.

The Aceternity focus-cards registry component was installed and adapted into
real keyboard-operable soap-bubble buttons. Motion uses shared `layoutId`
transitions and `MotionConfig reducedMotion="user"`, with a CSS reduced-motion
backstop. The facts companion uses the specified SVG/Framer fallback because a
suitable licensed cat Lottie asset was not available in the repository.

## Open questions

- Development-only backend tokens cannot authorize Supabase Storage. In that
  runtime, selected media stays as a local preview and no fake storage key is
  persisted. Production Supabase sessions use the real private bucket path.
- Behavior citations include a readable title, organization, and nullable URL.
  Unsafe or absent links retain the readable citation and render as plain text.
- Health claim `source_url` is optional in the API. Every claim still displays
  its corpus citation, but an external link cannot be rendered when the field is
  absent.
