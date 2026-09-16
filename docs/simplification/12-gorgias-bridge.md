# 12. Gorgias bridge sidecar — `console-src/helpdesk-agent/bridge/`

Read-only module analysis. Bridge stays dormant (`GORGIAS_BRIDGE_ENABLED=0`,
`HELPDESK_OUTBOUND_ENABLED=0`) per user preference (AGENTS.md:267). Activation
path preserved throughout.

## Snapshot

| File | LOC (`wc -l`) |
|---|---|
| `console-src/helpdesk-agent/bridge/__init__.py` | 9 |
| `console-src/helpdesk-agent/bridge/config.py` | 56 |
| `console-src/helpdesk-agent/bridge/gorgias_api.py` | 178 |
| `console-src/helpdesk-agent/bridge/gorgias_inbound.py` | 148 |
| `console-src/helpdesk-agent/bridge/email_out.py` | 92 |
| `console-src/helpdesk-agent/bridge/router.py` | 32 |
| `console-src/helpdesk-agent/tests/test_bridge.py` | 343 |
| `console-src/helpdesk-agent/bin/helpdesk` (CLI shim, not bridge-specific) | 8 |
| **Total** | **866** |

**Interface/seam.** `helpdesk/tissues.py` imports `bridge.config` and
`bridge.router` eagerly (tissues.py:15–16); `gorgias_api` and `email_out` are
lazy-imported only when the route selects them (tissues.py:166, 180). Inbound:
`bridge/gorgias_inbound.py` → `helpdesk.ingest_email` via `dispatch.invoke`
(gorgias_inbound.py:112–129), with `helpdesk.tickets` lazy-imported
(gorgias_inbound.py:102–103). The bridge's only HTTP door,
`console-src/inbox/review_server.py:105–108` (`POST /webhook/gorgias`), is a
**hardcoded 503 stub that never calls the bridge** — verified: repo-wide grep
for `gorgias_inbound`/`verify_secret` finds only `tests/test_bridge.py`; `git
log --all -S gorgias_inbound -- console-src/inbox/` is empty (never wired).

**Job description.** A dormant, dependency-free sidecar that would connect the
demo helpdesk to Gorgias when explicitly activated: inbound webhook parsing →
`helpdesk.ingest_email`, and human-confirmed outbound replies routed to the
Gorgias API or AgentMail email. All sends are double-locked — env flags plus the
hardcoded `helpdesk/send_access.py:9` lock — and the inbound HTTP door is
deliberately unwired on the isolated preview (PRODUCTION-READINESS-2026-09-07.md:29:
"Gorgias stays on the existing `/webhook/gorgias/*` receiver on port 8000").

**Dormancy economics (question a).** Rot is already prevented by the existing
gate: `tools/verify_release.sh:187–188` runs `unittest discover` over
`console-src/helpdesk-agent/tests` (includes `test_bridge.py`), and the
syntax-check roots include `console-src/helpdesk-agent` (verify_release.sh:85).
`test_bridge.py` is contract-tested against the live helpdesk API — it calls the
real `dispatch`/`invoke`/`handle_http` (test_bridge.py:43, 70, 76, 253–265),
so signature drift in `tickets`/`intake`/`dispatch` fails the gate. The
laziness contract is also tested: `test_gorgias_module_not_loaded_when_send_locked`
asserts `gorgias_api` never enters `sys.modules` on a locked send
(test_bridge.py:316–325). No new gate mechanism is needed. The two seams that
can still rot silently are (1) the unwired HTTP door — no test can drive
`accept()` through a real route because none exists — and (2) real network
behavior (all sends are mocked or lock-refused). Both are acceptable for a
dormant module; (1) is addressed by Action 1.

## Reinvented wheels

| # | Hand-rolled | Existing wheel | Evidence file:line | LOC saved | Switch risk |
|---|---|---|---|---|---|
| W1 | HTML-escape chain `text.replace("&","&amp;").replace("<","&lt;").replace(">",">").replace("\n","<br>")` | stdlib `html.escape` — already used by the production adapter (`webhook/src/bb_webhook/gorgias_client.py:225,342`: `html.escape(txt).replace("\n","<br>")`) | `bridge/gorgias_api.py:135` | ~1 | Low — output gains `&quot;`/`&#x27;`, aligning body_html with what production already sends Gorgias |
| — | Hand-built Basic auth header (`base64` of `email:key`) | `httpx`/`requests` `auth=` tuple — **rejected**: module is stdlib-only by design (gorgias_api.py:1); a new dep would have to enter the pinned, reviewed inbox venv | `bridge/gorgias_api.py:28–34` | 0 | Rejected (see below) |

No W3 (platform-feature reimplementations) found. The module is clean; this is
the single true reinvented wheel, and it is a parity bug more than a size win.

## Duplication map

### Within the bridge

1. **Send-lock re-check ×4** — identical guard in `gorgias_api.py:89–92`
   (`send_public_reply`), `gorgias_api.py:154–157` (`close_ticket`),
   `email_out.py:51–54`, plus the tissue-level check `tissues.py:101–105`.
   **Deliberate defense-in-depth on a safety lock — keep.** Each transport
   re-checking matters if it is ever called directly; collapsing to a
   decorator would reduce auditability of the lock.
2. **Two Gorgias boolean parsers** — `_is_customer` (gorgias_api.py:69–73) vs
   `_coerce_bool` (gorgias_inbound.py:31–37). Same wire quirk (stringly-typed
   `from_agent`), inverted semantics, same truthy set `{"true","1","yes"}`.
   ~8 LOC. Mergeable but low value; the inversion makes a shared helper easy
   to get wrong. P3 at best.
3. **`_attr`/`_as_list`/`_resolve_inbox_id`** — `email_out.py:9–39` vs
   `helpdesk/mailbox.py:68–94,196–208` (~30 LOC). **Not the same wheel**:
   `email_out._as_list` drops non-iterables (email_out.py:27) while
   `mailbox._as_list` wraps them (`mailbox.py:94`), and `mailbox` also matches
   `display_name` (mailbox.py:206). Semantics are load-bearing at their call
   sites; verifying equivalence costs more than the duplication. Keep separate.
   Inbound (mailbox, pull-only, mailbox.py:1) vs outbound (email_out, send-only)
   is otherwise a clean split (question c).

### vs the other two Gorgias adapters (question b)

| Concern | Bridge (`bridge/gorgias_api.py`) | Webhook (`webhook/src/bb_webhook/gorgias_client.py`) | MCP (`tools/gorgias_mcp.py`) |
|---|---|---|---|
| Transport | stdlib `urllib` (by design, :1) | `httpx` async | `requests` |
| URL build | env override + subdomain (:18–25) | settings + demo-mode localhost enforcement (:75,77–92) | `_bare_subdomain` (:39–48) |
| Auth | hand-built Basic header (:28–34) | httpx `auth` tuple (:76) | requests `auth` tuple (:49) |
| Error mapping | `RuntimeError` w/ 400-char detail (:65–66) | structured dicts + logging (:122–129) | `{"error":…}` dicts (:64–69) |
| 429 retry | fixed `0.5*attempt` backoff (:62–64) | honors `Retry-After`, bounded 10s (:46–52,209–218) | none |
| Send-reply mirror | loose copy (:87–150) | hardened: race guards `expected_recipient`/`expected_source_message_id` (:321–324), strict provider-message validation (:301–309) | n/a (read-only) |
| Delivery poll | :166–178 | :365–397 with identity + timestamp validation | n/a |

**Verdict: keep all three separate — duplication AS safety.** Evidence-based
reasons: (1) three different runtimes and credential sources (env-only sidecar
vs `get_settings()` vs `load_env()` file loader); (2) three different safety
roles — dormant demo, human-gated production write, read-only production
(AGENTS.md §2); (3) the bridge's stdlib-only constraint is load-bearing: it is
lazy-imported into the helpdesk/inbox process whose venv is pinned and reviewed
(`tools/check_inbox_locks.py`, `deploy/INBOX-NETWORK-ISOLATION.md`), so a shared
helper would either drag a dep in or force a cross-tree import that crosses the
webhook deploy boundary; (4) the send-reply duplication is *deliberately
different* — production carries race guards the human-gated demo does not need.
A bug merged into one shared client would reach all three blast radii at once.
No piece (URL build, auth header, error map) is worth extracting into one
place; even "low-level" sharing couples the dormant demo's release cadence to
the production auto-deploy.

### `bridge/config.py` vs `helpdesk/env.py` (question d)

**Not duplicates — keep separate.** `env.py:22–39` parses the root `.env` *file*
(Shopify keys); `bridge/config.py` reads `os.environ` only, presence checks,
never values (config.py:1,13–14,32–45). The split is a containment property:
the isolated inbox "must never try to traverse /root or load the application's
.env" (review_server.py:27–28). Merging would give the bridge a `.env`-reading
path the isolated runtime explicitly forbids.

## Reliability risks

| # | Scenario | Consequence | Minimal fix | Evidence |
|---|---|---|---|---|
| R1 | A future activation wires `accept()` into the HTTP door and forgets `verify_secret` — `accept()` checks **only** the env flag, never the secret; `verify_secret` has zero production callers; the doc that would show the wiring (`deploy/GORGIAS-BRIDGE-SETUP.md`) is referenced but missing and never existed in git history | Unauthenticated ticket-injection door into the demo intake the moment the bridge is switched on | Write the missing `deploy/GORGIAS-BRIDGE-SETUP.md` with an explicit wiring snippet (`verify_secret(headers)` before `accept()`); optionally a 3-line headers gate inside `accept()` | gorgias_inbound.py:92–101 (no secret check), :13–28 (separate function); grep: only tests/test_bridge.py references it; AGENTS.md:282 and helpdesk-design/INTAKE.md:8 point to `deploy/GORGIAS-BRIDGE-SETUP.md` (absent; `git log --all` on the path is empty) |
| R2 | Bridge activated; outbound call fails below HTTP (the isolated inbox denies `connect()` with EPERM, or any DNS/timeout failure) — `_request` catches only `HTTPError`, so `URLError` propagates raw; `email_out` catches broad `Exception` | The two transports fail inconsistently: Gorgias path produces an opaque 500 (`internal_error`, review_server.py:168–170) instead of the structured `{"ok":false,"error":…}` every other path returns — a dormant-then-rotting module fails ugly on first real use | Catch `urllib.error.URLError` (+ `json.JSONDecodeError`) in `_request` and map to the same `RuntimeError` | gorgias_api.py:57–66 (HTTPError only) vs email_out.py:84–85 (broad catch); deploy/INBOX-NETWORK-ISOLATION.md:3 (connect EPERM) |
| R3 | `int(ticket_id)` raises uncaught `ValueError` if `external.ticketId` is non-numeric | 500 on a send instead of structured error. Hunch: Gorgias ids are ints today; `parse_event` passes the raw webhook value through | Guard in `send_public_reply`/`close_ticket`, return `{"ok":false,"error":"invalid gorgias ticket id"}` | gorgias_api.py:96,158; gorgias_inbound.py:124 stores `str(event["ticket_id"])` |
| R4 | Invariant test blind spot: `test_does_not_import_gorgias_wrappers` scans `ImportFrom` module names only — `from bridge import gorgias_api` is invisible to it; a top-level hoist of the lazy import (tissues.py:166) would pass the safety scan | The stated invariant "helpdesk/ never imports gorgias_*" (bridge/__init__.py:3) is enforced only weakly; laziness itself is still runtime-tested. Hunch on materiality | Also scan `ImportFrom` aliases for `"gorgias"` (~3 lines in the test) | tests/test_safety.py:33–41 vs tissues.py:166 |
| R5 | Stale API surface: `verify_secret`'s `query_secret` param kept "for review_server callers" — no such caller exists | None (ghost parameter, but the comment documents the log-leak rationale — query secrets refused, tested) | None needed; resolves itself when R1's doc names the real wiring | gorgias_inbound.py:26–27; test_bridge.py:136–137 |

**Inbound verification correctness (question e):** the bridge does **not** use
HMAC-over-body like production's method 1 (webhook_handler.py:261–268,
HMAC-SHA256 of raw body). It is a header-only shared-secret compare —
constant-time via `hmac.compare_digest` (gorgias_inbound.py:28), fail-closed
when unset (:16–18), and deliberately refuses query secrets (log-leak) while
production accepts them as fallback (webhook_handler.py:278–286). The code is
correct and tested (test_bridge.py:131–137) — different scheme, same rigor,
simpler because Gorgias HTTP Integrations cannot sign bodies.

**Router (question f):** 32 lines, two branches, no growth. Fine as-is.

## Simplification actions

| # | Action | Files | LOC Δ | Risk | Priority | Longevity gain |
|---|---|---|---|---|---|---|
| 1 | Write `deploy/GORGIAS-BRIDGE-SETUP.md` — the activation doc AGENTS.md:282 and INTAKE.md:8 already reference but which does not exist. Contents: env ladder (`GORGIAS_BRIDGE_ENABLED`, `GORGIAS_SUBDOMAIN/API_EMAIL/API_KEY/BRIDGE_SECRET` per config.py:32–41; `HELPDESK_OUTBOUND_ENABLED`, `HELPDESK_SEND_ALLOWLIST`, `AGENTMAIL_API_KEY`), the three deliberate code locks that env flags alone cannot flip (`send_access.py:9` hardcoded False; review_server.py:105–108 inbound stub; systemd connect-denial per INBOX-NETWORK-ISOLATION.md:3,5), and the inbound wiring snippet that calls `verify_secret` **before** `accept` | `deploy/GORGIAS-BRIDGE-SETUP.md` (new) | +~60 doc | None | **P1** | High — this *is* the activation-path preservation the user asked for; today the path exists only as tribal knowledge plus two dangling references |
| 2 | Use stdlib `html.escape` for `body_html` — parity with the production adapter | `bridge/gorgias_api.py:135` | −1 | Low | P2 | Correctness parity with gorgias_client.py:342 |
| 3 | Map `URLError`/`JSONDecodeError` to the structured `RuntimeError` in `_request` so a freshly-activated bridge fails like every other path | `bridge/gorgias_api.py:54–66` | +3 | Low | P2 | Fail-closed with a readable error after months of dormancy |
| 4 | Drop dead imports (`threading`, `BaseHTTPRequestHandler`, `ThreadingHTTPServer`) — leftovers of a removed local HTTP mock | `tests/test_bridge.py:9,11` | −2 | None | P3 | Trivial hygiene |
| 5 | Guard non-numeric `ticket_id` before `int()` | `bridge/gorgias_api.py:96,158` | +4 | None | P3 | Robustness against a provider format change |
| 6 | Extend `test_does_not_import_gorgias_wrappers` to scan `ImportFrom` aliases | `tests/test_safety.py:36–41` | +3 | None | P3 | Closes the invariant-enforcement gap (R4) |

## Rejected on principle

- Delete or trim the bridge, its tests, or its flags — user wants the activation
  path preserved (AGENTS.md:267); the gate already pays its ~350 test-LOC rent.
- One shared Gorgias client for the three adapters — three runtimes, three
  credential sources, three safety roles; stdlib-only is load-bearing
  (gorgias_api.py:1); blast radius of a shared bug reaches the production
  auto-deploy (duplication AS safety — verified verdict, question b).
- Add `httpx`/`requests` to the bridge — would have to enter the pinned, reviewed
  inbox runtime venv (check_inbox_locks.py; INBOX-NETWORK-ISOLATION.md).
- Merge `email_out` helpers into `helpdesk/mailbox.py` — `_as_list` semantics
  differ (drop vs wrap); bridge→helpdesk imports are lazy-by-design
  (gorgias_inbound.py:102).
- Merge `bridge/config.py` into `helpdesk/env.py` — file-loader vs
  process-env-only is a containment property (review_server.py:27–28).
- Collapse the 4× `send_access` re-checks — defense-in-depth on a safety lock
  beats DRY.
- Wire the inbound door, flip any flag, or add a bridge systemd unit now.

## Verification

- **Action 1 (P1, docs):** the wiring snippet is validated by existing tests —
  `test_verify_secret_bearer_and_header` (test_bridge.py:131–137) and
  `test_bridge_disabled_returns_503` (:189–193). If the optional `headers`
  gate inside `accept()` is added: ONE new test (~6 lines, mirrors
  test_bridge.py:131) — `accept()` with wrong/absent secret returns 401.
- **Action 2 (P2):** ONE new test (~10 lines, offline): patch
  `helpdesk.send_access.send_access_enabled` → True and `bridge.gorgias_api._request`
  to capture the payload, call `send_public_reply`, assert `body_html ==
  html.escape(text).replace("\n", "<br>")`. (Existing tests only cover locked
  sends with mocks — test_bridge.py:290–314 — so this is genuinely new coverage.)
- **Action 3 (P2):** ONE new test (~6 lines): patch
  `urllib.request.urlopen` to raise `URLError("connect EPERM")`, assert
  `_request` raises the structured `RuntimeError` (not a raw URLError).
- **Actions 4–6 (P3):** the existing suite re-run covers them
  (verify_release.sh:187–188); Action 6 is itself a test.
