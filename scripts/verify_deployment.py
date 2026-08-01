"""Exercise a *deployed* backend from outside it. Never point this at localhost.

    python scripts/verify_deployment.py \
        --api https://cat-companion-api.up.railway.app \
        --origin https://whisker-rooms.example.workers.dev \
        --email you+verify@example.com --password '<password>'

Covers the automatable parts of the Part C checklist: probes, CORS, cat
isolation, the deterministic emergency gate, the honest health empty state,
export, cascade delete, and typed errors. It deliberately does **not** cover
anything that needs a human or a browser — clicking a confirmation link,
uploading a photo through the file picker, reading the browser console. Those
stay manual; DEPLOYMENT.md lists them.

The script creates a real account and real cats in the target project and then
deletes the account. Run it against production with a throwaway address.

Exit code is 0 only when every automated check passes.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any

import httpx


class Report:
    """Collects pass/fail lines so one failure does not hide the rest."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.notes: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{f' — {detail}' if detail else ''}")
        if not ok:
            self.failures.append(f"{name}{f': {detail}' if detail else ''}")
        return ok

    def note(self, text: str) -> None:
        print(f"  [NOTE] {text}")
        self.notes.append(text)


def _cat_payload(cat_id: uuid.UUID, name: str) -> dict[str, Any]:
    return {
        "cat_id": str(cat_id),
        "name": name,
        "age": {"value": 3, "unit": "years"},
        "breed": None,
        "sex": "unknown",
        "weight": {"value": 9, "unit": "lb"},
        "energy_level": 3,
        "common_patterns": "Knocks pens off the desk.",
        "known_conditions": [],
        "photo_references": [],
        "theme": {"primary_color": "#112233", "accent_color": "#AABBCC"},
    }


def verify(api: str, origin: str, email: str, password: str) -> Report:
    report = Report()
    api = api.rstrip("/")
    client = httpx.Client(base_url=api, timeout=60, follow_redirects=False)

    # -- B1: probes, unauthenticated, no configuration detail -----------------
    print("\n1. Liveness and readiness")
    health = client.get("/health")
    report.check("GET /health is 200 without auth", health.status_code == 200,
                 f"got {health.status_code}")
    ready = client.get("/ready")
    report.check("GET /ready is 200 without auth", ready.status_code == 200,
                 f"got {ready.status_code} {ready.text[:120]}")
    leaked = [
        word
        for word in ("supabase", "postgres", "anthropic", "claude", "voyage", "key")
        if word in (health.text + ready.text).casefold()
    ]
    report.check("probes leak no configuration", not leaked, f"found {leaked}")

    # -- Hard requirement 5: nothing unauthenticated leaks internals ----------
    print("\n2. Unauthenticated surface")
    for path in ("/cats", "/account/export", "/no-such-route"):
        response = client.get(path)
        body = response.text
        report.check(
            f"{path} returns a typed error, no stack trace",
            response.status_code in {401, 403, 404, 409}
            and "Traceback" not in body
            and "File \"" not in body,
            f"status {response.status_code}",
        )
    docs = client.get("/openapi.json")
    if docs.status_code == 200:
        report.note(
            "/openapi.json is publicly served. It exposes the full schema but no "
            "configuration values; disable it if that is not wanted."
        )

    # -- A6: CORS ------------------------------------------------------------
    print("\n3. CORS")
    preflight = client.options(
        "/chat/behavior",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,x-active-cat-id",
        },
    )
    allow_origin = preflight.headers.get("access-control-allow-origin")
    report.check(
        f"preflight from {origin} is allowed",
        preflight.status_code == 200 and allow_origin == origin,
        f"status {preflight.status_code}, allow-origin {allow_origin!r}",
    )
    hostile = client.options(
        "/chat/behavior",
        headers={
            "Origin": "https://not-the-frontend.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    report.check(
        "an unlisted origin is refused",
        hostile.headers.get("access-control-allow-origin") is None,
        f"allow-origin {hostile.headers.get('access-control-allow-origin')!r}",
    )

    # -- A5: sign-up returns confirmation-pending ----------------------------
    print("\n4. Sign-up and email confirmation")
    signup = client.post("/auth/sign-up", json={"email": email, "password": password})
    if signup.status_code in {200, 201}:
        signup_body: dict[str, Any] = signup.json()
        status = signup_body.get("status")
        if status == "confirmation_required":
            report.check("sign-up returns confirmation_required", True)
            report.check(
                "confirmation-pending response carries no tokens",
                signup_body.get("access_token") is None
                and signup_body.get("refresh_token") is None,
            )
            report.note(
                "Email confirmation is ON. Confirm the address in the inbox, then "
                "re-run with --skip-signup to exercise the authenticated checks."
            )
        else:
            report.check("sign-up returned an active session", status == "active")
            report.note(
                "Supabase email confirmation appears to be DISABLED for this "
                "project — production requires it enabled (A5)."
            )
    elif signup.status_code == 409:
        report.note("account already exists; continuing to sign-in")
    else:
        report.check("sign-up succeeded", False,
                     f"status {signup.status_code} {signup.text[:160]}")

    signin = client.post("/auth/sign-in", json={"email": email, "password": password})
    if signin.status_code == 403 and "EMAIL_NOT_CONFIRMED" in signin.text:
        report.check("unconfirmed sign-in is a typed 403, not a 500", True)
        report.note(
            "Stopping here: the address is not confirmed yet, so no authenticated "
            "checks can run. Click the emailed link and re-run this script."
        )
        client.close()
        return report
    if signin.status_code != 200 or signin.json().get("status") != "active":
        report.check("sign-in returned an active session", False,
                     f"status {signin.status_code} {signin.text[:160]}")
        client.close()
        return report

    token = signin.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    report.check("sign-in returned an active session", True)

    # -- Cats and isolation --------------------------------------------------
    print("\n5. Cats, chat, and isolation")
    cat_a, cat_b = uuid.uuid4(), uuid.uuid4()
    for cat_id, name in ((cat_a, "Mochi"), (cat_b, "Pepper")):
        created = client.post("/cats", headers=auth, json=_cat_payload(cat_id, name))
        report.check(f"created cat {name}", created.status_code == 201,
                     f"status {created.status_code} {created.text[:160]}")

    def chat(path: str, cat_id: uuid.UUID, message: str) -> httpx.Response:
        body: dict[str, Any] = {
            "cat_id": str(cat_id),
            "message": message,
            "session_id": str(uuid.uuid4()),
        }
        if path.endswith("health"):
            body["intake"] = None
        return client.post(
            path,
            headers={**auth, "X-Active-Cat-ID": str(cat_id)},
            json=body,
        )

    # Part C item 3 — behavior answer with a mode indicator.
    behavior = chat("/chat/behavior", cat_a, "Why does my cat knock things off tables?")
    if report.check("behavior query answered", behavior.status_code == 200,
                    f"status {behavior.status_code} {behavior.text[:160]}"):
        payload = behavior.json()
        mode = payload.get("interpretation", {}).get("answer_mode")
        report.check(
            "behavior response carries an answer_mode indicator",
            mode in {"corpus_grounded", "general_knowledge"},
            f"answer_mode={mode!r}",
        )

    # Part C item 4 — the deterministic emergency gate, with no model call.
    emergency = chat(
        "/chat/health", cat_a,
        "My cat can't pee and keeps straining in the litter box",
    )
    if report.check("emergency health query answered", emergency.status_code == 200,
                    f"status {emergency.status_code} {emergency.text[:160]}"):
        result = emergency.json().get("result", {})
        report.check(
            "emergency query is coded emergency",
            result.get("severity") == "emergency",
            f"severity={result.get('severity')!r}",
        )
        report.check(
            "emergency response makes no grounded claims (no model call)",
            result.get("claims") == [],
            f"claims={len(result.get('claims') or [])}",
        )

    # Part C item 5 — the honest empty state.
    empty = chat("/chat/health", cat_a, "Does my cat enjoy Baroque harpsichord music?")
    if report.check("off-corpus health query answered", empty.status_code == 200,
                    f"status {empty.status_code}"):
        kind = empty.json().get("result", {}).get("response_kind")
        report.check(
            "off-corpus health query returns no_reliable_information",
            kind == "no_reliable_information",
            f"response_kind={kind!r}",
        )

    # Part C item 6 — cat isolation.
    mismatched = client.post(
        "/chat/behavior",
        headers={**auth, "X-Active-Cat-ID": str(cat_b)},
        json={
            "cat_id": str(cat_a),
            "message": "Whose cat is this?",
            "session_id": str(uuid.uuid4()),
        },
    )
    report.check(
        "a cat_id/active-cat mismatch is refused",
        mismatched.status_code == 409
        and mismatched.json().get("code") == "CAT_SCOPE_MISMATCH",
        f"status {mismatched.status_code} {mismatched.text[:120]}",
    )
    foreign = client.post(
        "/chat/behavior",
        headers={**auth, "X-Active-Cat-ID": str(uuid.uuid4())},
        json={
            "cat_id": str(uuid.uuid4()),
            "message": "Whose cat is this?",
            "session_id": str(uuid.uuid4()),
        },
    )
    report.check(
        "a cat this account does not own is refused",
        foreign.status_code in {403, 409},
        f"status {foreign.status_code}",
    )

    # -- B4 — rate limiting --------------------------------------------------
    print("\n6. Rate limiting")
    statuses = [
        chat("/chat/behavior", cat_a, f"probe {n}").status_code for n in range(25)
    ]
    if 429 in statuses:
        report.check("per-account chat rate limit engages", True,
                     f"429 after {statuses.index(429)} requests")
    else:
        report.note(
            "no 429 within 25 requests — either the limit is above 25 or more than "
            "one replica is serving. Confirm CHAT_RATE_LIMIT_PER_MINUTE and replica "
            "count; the limiter is per process."
        )

    # -- Part C item 7 — export, then cascade delete -------------------------
    print("\n7. Export and account deletion")
    export = client.get("/account/export", headers=auth)
    if report.check("account export succeeds", export.status_code == 200,
                    f"status {export.status_code}"):
        exported = export.json()
        names = json.dumps(exported)
        report.check("export contains both cats", "Mochi" in names and "Pepper" in names)

    deleted = client.delete("/account", headers=auth)
    report.check("account delete succeeds", deleted.status_code == 200,
                 f"status {deleted.status_code} {deleted.text[:160]}")
    after = client.get("/account/export", headers=auth)
    report.check(
        "the deleted account's data is gone (cascade)",
        after.status_code in {401, 404},
        f"status {after.status_code}",
    )

    client.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--api", required=True, help="Deployed backend base URL")
    parser.add_argument("--origin", required=True, help="Deployed frontend origin")
    parser.add_argument("--email", required=True, help="Throwaway account address")
    parser.add_argument("--password", required=True, help="At least 8 characters")
    args = parser.parse_args()

    for value, label in ((args.api, "--api"), (args.origin, "--origin")):
        if "localhost" in value or "127.0.0.1" in value:
            parser.error(
                f"{label}={value!r} points at localhost. This script exists to "
                "exercise the deployed URLs; a local pass proves nothing about "
                "production configuration."
            )

    print(f"Verifying {args.api} with frontend origin {args.origin}")
    report = verify(args.api, args.origin, args.email, args.password)

    print("\n" + "=" * 68)
    if report.failures:
        print(f"{len(report.failures)} check(s) FAILED:")
        for failure in report.failures:
            print(f"  - {failure}")
    else:
        print("All automated checks passed.")
    if report.notes:
        print("\nNotes:")
        for note in report.notes:
            print(f"  - {note}")
    print(
        "\nStill manual (needs a browser or an inbox): email confirmation click, "
        "photo upload to Supabase Storage, browser console errors, and the spend "
        "ledger reading (`python -m app.ops.spend show`)."
    )
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
