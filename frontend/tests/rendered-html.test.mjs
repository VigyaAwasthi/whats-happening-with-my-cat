import assert from "node:assert/strict";
import { access, readFile, stat } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

async function source(path) {
  return readFile(new URL(path, root), "utf8");
}

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html", host: "localhost" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the Whisker rooms application shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /Whisker rooms — a thoughtful home for life with cats/i);
  assert.match(html, /Opening the rooms/);
  assert.doesNotMatch(html, /Your site is taking shape|Building your site/);
});

test("keeps the health surface structurally fail-closed", async () => {
  const [health, chat, urlSafety, css] = await Promise.all([
    source("components/cat-app/health.tsx"),
    source("components/cat-app/chat.tsx"),
    source("lib/url.ts"),
    source("app/theme.css"),
  ]);

  assert.match(health, /response_kind === "emergency_canned"/);
  assert.match(health, /response_kind === "no_reliable_information"/);
  assert.match(health, /result\.claims\.length > 0/);
  assert.match(
    health,
    /result\.retrieved_entry_ids\.includes\(claim\.source_entry_id\)/,
  );
  assert.match(health, /We cannot safely show this answer/);
  assert.match(health, /new Map/);
  assert.match(health, /safeExternalUrl\(claim\.source_url\)/);
  assert.match(chat, /safeExternalUrl\(citation\.url\)/);
  assert.match(urlSafety, /value\.trim\(\) !== value/);
  assert.match(urlSafety, /\/\\s\/\.test\(value\)/);
  assert.match(urlSafety, /lowered\.includes\("verify"\)/);
  assert.match(health, /health-boundary/);
  assert.match(health, /go to a veterinarian now/i);

  const emergencyRule =
    css.match(/\.emergency-block\s*\{[^}]+\}/)?.[0] ?? "";
  assert.match(emergencyRule, /background:\s*var\(--ink\)/);
  assert.match(emergencyRule, /color:\s*var\(--cream\)/);
  assert.doesNotMatch(emergencyRule, /vermillion|raspberry|#E43D12|#D6536D/i);
});

test("preserves cat scope, server-returned sessions, and grounded UI text", async () => {
  const [api, app, chat, moments] = await Promise.all([
    source("lib/api.ts"),
    source("app/CatCompanionApp.tsx"),
    source("components/cat-app/chat.tsx"),
    source("components/cat-app/moments.tsx"),
  ]);

  assert.match(api, /"X-Active-Cat-ID": catId/);
  assert.match(api, /whisker-rooms:no-active-cat/);
  assert.match(app, /setBehaviorSessionId\(crypto\.randomUUID\(\)\)/);
  assert.match(app, /setHealthSessionId\(crypto\.randomUUID\(\)\)/);
  assert.match(app, /setBehaviorMessages\(\[\]\)/);
  assert.match(app, /setHealthExchanges\(\[\]\)/);
  assert.match(chat, /onSessionId\(response\.session_id\)/);
  assert.match(chat, /setDraft\(question\)/);
  assert.match(moments, /Never read by AI/);
  assert.doesNotMatch(moments, /catApi\.(behavior|health|facts)/);
});

test("ships keyboard, reduced-motion, typography, and social metadata contracts", async () => {
  const [focusCards, css, layout, facts, image] = await Promise.all([
    source("components/ui/focus-cards.tsx"),
    source("app/theme.css"),
    source("app/layout.tsx"),
    source("components/cat-app/facts.tsx"),
    stat(new URL("../public/og.png", import.meta.url)),
  ]);

  assert.match(focusCards, /<motion\.button/);
  assert.match(focusCards, /onFocus=/);
  assert.match(focusCards, /aria-label=/);
  assert.match(css, /:focus-visible/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(css, /animation:\s*none !important/);
  assert.match(layout, /Manrope/);
  assert.match(layout, /Newsreader/);
  assert.match(layout, /summary_large_image/);
  assert.match(layout, /new URL\("\/og\.png", metadataBase\)/);
  assert.match(facts, /<svg/);
  assert.match(facts, /reducedMotion/);
  assert.ok(image.size > 100_000);

  await assert.rejects(
    access(new URL("../app/_sites-preview/SkeletonPreview.tsx", import.meta.url)),
  );
});
