# 09 — whatsapp-connect (simplification analysis)

Read-only analysis, branch `refactor`, 2026-09-16. Scope: `whatsapp-connect/`
(QR pairing + owner alerts + 2-way Hermes bridge) and its deploy seams.
Safety model held: escalation stays on this local Baileys bridge (no Twilio —
`tools/verify_release.sh:155` hard-fails on it), the 2-way bridge must not load
credentials or expose a Gorgias write path.

## Snapshot

| File | LOC | Role |
|---|---|---|
| `whatsapp-connect/server.js` | 429 total | Logic ~lines 1–298 (~200 real LOC); lines 299–429 are the embedded pairing `PAGE` (~131 lines, ~104 KB incl. base64 brand fonts) |
| `whatsapp-connect/security.js` | 90 | Secret validation, constant-time Bearer/Basic check, loopback-only `X-Forwarded-For` |
| `whatsapp-connect/test/security.test.js` | 102 | Auth fail-closed tests (the only test dir the offline gate runs) |
| `whatsapp-connect/qs-security.test.js` | 15 | Locks pinned `qs` 6.16.0 behavior (module root) |
| `whatsapp-connect/startup-log.test.js` | 15 | Locks loopback bind + no token in startup log (module root) |
| `whatsapp-connect/package.json` | 19 | baileys ^6.7.9 (resolves 6.7.23), express ^4.19.2 (4.22.2), pino ^9.5.0, qrcode 1.5.4; `overrides: qs 6.16.0` |
| `whatsapp-connect/package-lock.json` | ~93 KB | lockfileVersion 3, top-level `node_modules/qs` = 6.16.0 |
| `whatsapp-connect/buttonsbebe-whatsapp-connect.service` | 18 | Module-local near-twin of `deploy/systemd/` unit |
| `deploy/systemd/buttonsbebe-whatsapp-connect.service` (+ `.service.d/password.conf`) | 18 + 2 | Supported unit: `Restart=on-failure`, `RestartSec=3`, env via unit + drop-in |
| `whatsapp-connect/Caddyfile` | 7 | **RETIRED** (marked, `deploy/tests/test_caddy_config.py:176` asserts it stays retired) |
| `whatsapp-connect/README.md` / `.env.example` | 24 / 5 | Three-secrets contract doc |

Interface/seam: binds `127.0.0.1:8085` (`server.js:293`). Public via Caddy only
at `/connect-whatsapp/<WA_TOKEN>/*` (`deploy/caddy/sites/support.caddy:23–26`,
logs strip the URI at :140–146); console reaches the JSON API at
`/console/waapi/* → /wa/*` behind `forward_auth` (`support.caddy:77–84`).
Alerts arrive as `POST ${BASE}/send` from `processor/whatsapp_notifier.py`
(urllib + Bearer, `processor/whatsapp_notifier.py:92–186`) and
`processor/heartbeat.sh` (curl, `processor/heartbeat.sh:110–118`). 2-way bridge:
owner self-chat messages filtered at `server.js:152–153`, forwarded via
`execFile(HERMES_BIN, ["-z", text])` (`server.js:166–184`).

Job description: keep a personal WhatsApp account linked via Baileys
(multi-file auth in `WA_AUTH_DIR`), render the pairing QR to the owner, accept
authed alert POSTs and deliver them to the configured destination (linked
owner or typed number), and let the owner chat Hermes from their own
"Note to Self" chat. Longevity posture is sound: everything pinned by
`package-lock.json` + `npm ci` in CI (`.github/workflows/ci.yml:59–70`); the
`^6.7.9` caret on Baileys is a fast-moving lib but the lock governs installs.

## Reinvented wheels

| # | Hand-rolled | Existing wheel | Evidence | LOC saved | Switch risk |
|---|---|---|---|---|---|
| W1a | `audit()` + bare `console.log/error` string logging; pino imported and used *only* to silence Baileys (`P({level:"silent"})`) | pino is already a direct dep — use it for the service's own logs too (or drop the dep and pass a 5-line noop logger) | `server.js:21,49–51,96`; `package.json:16` | ~5–10 net, plus one convention | Low |
| W1b | Entire inline pairing page `PAGE` (~131 lines / 104 KB, embedded fonts) + its Basic-auth gate, `WA_PASSWORD` secret, and `/status` route | The console SPA already renders the same QR + identical "Open WhatsApp → Linked devices" steps in its Notifications tab behind session auth; brand pipeline already regenerates the page | `server.js:289–291,299–429,202–209,217–219,33,35–37`; `console-src/index.html:797–806`; `tools/sync_console_brand.py:9` | ~150 LOC + 104 KB + one public route + one secret | Medium — owner UX decision; `/send` shares the token path so the Caddy route can only be dropped if the alert POST is verified localhost-first |
| W2a | Alert POST client implemented 3× (notifier urllib, heartbeat curl, plus demo stub path) | Contract is deliberate independence + locked by test (`processor/test_heartbeat.py:140`) — keep, document | `processor/whatsapp_notifier.py:110–186`; `processor/heartbeat.sh:96–119` | 0 (accepted) | n/a |
| W2b | Module-local systemd unit near-twin of `deploy/systemd/` unit (diff = env lines only; `EnvironmentFile` vs inline redacted) | `deploy/` is the only supported source per AGENTS.md §5 | `whatsapp-connect/buttonsbebe-whatsapp-connect.service` vs `deploy/systemd/...` (verified by diff) | 18 (delete) | Low — update README/HANDOVER references |
| W3 | Reconnect uses Baileys' own `connection.update`/`DisconnectReason` events (correct — Baileys has no auto-reconnect); the hand-rolled `setTimeout` retry at fixed 2 s has no backoff, no attempt cap, and **does not reset `state`** | Platform events are already the wheel being used; missing state regression is a bug, not a missing wheel | `server.js:118–142` | n/a (see Reliability risks R1/R2) | n/a |

Not reinvented (verified good): `fetchLatestBaileysVersion` with bundled
fallback (`server.js:85–90`); `qs` pinned via npm `overrides` rather than a
vendored patch (`package.json:18–20`, commit 36b43bb); `useMultiFileAuthState`
persists creds so VPS reboots need no re-pairing (`server.js:84,99`);
logged-out wipes `AUTH_DIR` and re-QRs instead of looping on dead creds
(`server.js:123–137`).

## Duplication map

Within module:
- QR pairing steps text duplicated verbatim in the embedded page and the
  console Notifications tab (`server.js:380–384` vs `console-src/index.html:804–805`).
- `GET ${BASE}/status` and `GET /wa/status` are twin handlers (the latter is a
  superset adding `notify`): `server.js:217–219` vs `server.js:245–248`.
- `notify.json` written non-atomically with `writeFileSync` (`server.js:66–68`)
  while `kb-admin/server.js:36–63` has a full atomicSave pattern for the same
  class of tiny JSON state file.

vs other modules:
- Alert sender triplicated: `processor/whatsapp_notifier.py` (urllib, retries,
  backoff), `processor/heartbeat.sh:96–119` (curl, fail-soft), and the demo
  endpoint path (`whatsapp_notifier.py:100–117`). Deliberate: the dead-man's
  switch must work when the processor's venv is broken; the auth contract is
  test-locked (`processor/test_heartbeat.py:140`). Keep; document the reason.
- Two Node server styles live side by side: express here (`server.js:17,199–200`)
  vs zero-dep `http` in kb-admin (`kb-admin/server.js:1–11,` "zero-dependency
  Node"). Both defensible — kb-admin is small enough to review dep-free;
  whatsapp-connect is already deep in npm-land via Baileys. Recommend the
  documented-reason option (one paragraph in AGENTS.md §5 or README), not a
  port.
- Module-local systemd unit + retired Caddyfile duplicate `deploy/` sources
  (see W2b). The Caddyfile is already stamped RETIRED; the unit is not.
- Logging style differs from the processor's `logging_setup.log_event`
  (level/logger/msg JSON, parsed by `tools/ops/monitor.py:50–58`).
  whatsapp-connect's `audit()` emits `{at,event,...}` JSON plus bare strings —
  nothing parses whatsapp-connect logs today, so this is convention debt, not
  a break.

## Reliability risks

**Status (2026-09-16, after Wave 1 of this plan landed in PR #28): R1's
hold-and-retry, R2 and R3 all shipped — the `/send` handler retries retryable
reconnect failures within its window (and `sendWithRetry` now clamps every
sleep to the advertised `maxWaitMs` deadline and rechecks before the next
attempt); `state` regresses to `"connecting"` on non-loggedOut close; the
reconnect loop resets its failure counter only on a live
`connection === "open"` and reschedules itself after a failed attempt (so
the cap is reachable and hands control back to systemd's
`Restart=on-failure`). R1's optional durable spool and R4–R7 are still open.
The row-429 / `server.js` line numbers below are the pre-Wave-1 snapshot and
now drift; re-check against the file before citing them.**

R1 — **Alert lost to a Baileys reconnect window (alert-loss first).**
Scenario: notifier itself documents that the bridge "disconnects and
reconnects every ~2.5 minutes" (`processor/whatsapp_notifier.py:88–91`). An
alert POST landing while the socket is down gets 409
(`server.js:189–191,236–238`). The durable alert path deliberately uses
`max_retries=0` (`processor/orchestrator.py:583–590`) because a *timeout* may
follow an accepted send — but a fast 409 from the pre-check
(`state !== "connected" || !jid`) is *definitively not sent*, and the bridge
does not retry it. Consequence: no WhatsApp message for a HIGH/CRITICAL
ticket; the only trace is the "uncertain" DB row surfaced as a console banner
(`webhook/src/bb_webhook/database.py:816–820`;
`console-src/index.html:524,541`) — which requires the owner to open the
console, defeating the point of a phone alert. A chargeback can sit unseen.
Minimal fix (bridge-side, ~12 LOC): in the `/send` handler, hold the request
and retry `sendAlert` for up to ~60 s (e.g., every 5 s) before answering —
the reconnect loop reopens in ~2 s (`server.js:140`), so the common window is
covered without any idempotency ambiguity (the pre-check rejection never
reached `sendMessage`). Durable option (P3): small on-disk spool drained on
`connection === "open"`.

R2 — **Stale `state="connected"` while disconnected.** On a non-loggedOut
close the handler reconnects but never resets `state`
(`server.js:138–141` — no assignment; only the loggedOut branch sets `"qr"`).
Consequence: console Connections/Notifications tabs show "Connected" while
the socket is down (`console-src/index.html:707–710` trusts `state`), and
`sendAlert`'s guard passes and calls `sendMessage` on a dead socket
(`server.js:189–192`), converting a clean fast rejection into an exception
path. Minimal fix: set `state = "connecting"` in the close branch (1 LOC) —
the console already renders unknown states as "Connecting"
(`console-src/index.html:708–710`).

R3 — **Reconnect failure leaves a zombie service.** `setTimeout(() =>
startSock().catch(e => console.error(e)))` (`server.js:137,140`): if
`startSock` keeps throwing (e.g., `AUTH_DIR` unreadable after a partial
wipe), there is no backoff, no attempt cap, and — combined with R2 — the
service still reports `state="connected"` with a null socket, forever.
Consequence: systemd sees a healthy unit; the ops monitor only checks service
active + TCP port (`tools/ops/monitor.py:16–20,96`), both of which stay green
because express still serves. Alerts silently dead until someone opens the
console. Minimal fix: R2's state regression makes `/wa/status` truthful; the
deploy readiness probe already treats QR-vs-connected as business health
(`deploy/cd/buttonsbebe-deploy-receive.sh:166–169`). Cap the retry loop (e.g.,
after N failures exit non-zero so `Restart=on-failure` takes over,
`deploy/systemd/buttonsbebe-whatsapp-connect.service:13–14`) ~5 LOC.

R4 — **Dependency freshness on the VPS after the `qs` pin.** The lock pins
`qs` 6.16.0 (`whatsapp-connect/package-lock.json`, top-level `node_modules/qs`)
but the local `node_modules` still holds 6.15.3 (verified:
`node -e require(...)`) and `npm test` fails 2 of 3 qs-security tests locally.
CD ships the manifests but never runs `npm ci`
(`deploy/cd/source_release.py:34,39`; no npm step in
`buttonsbebe-deploy-receive.sh`). Consequence (hunch — cannot read the VPS):
if the VPS `node_modules` predates the Sep-7 pin commit 36b43bb, the *live*
service runs qs 6.15.3 and the CI-green qs tests do not describe production.
Minimal fix: one `npm ci` in the deploy receive script for this service, or a
documented post-pin VPS step + a version echo in `/wa/status`.

R5 — **No express error middleware; `NODE_ENV` unset.** All current handlers
try/catch (verified per-route), but any future sync throw reaches express's
default handler which prints a stack trace to the client when `NODE_ENV` is
not `production` — and the unit sets no `NODE_ENV`
(`deploy/systemd/buttonsbebe-whatsapp-connect.service`). Latent, publicly
reachable via the token path. Minimal fix: 4-line error middleware +
`Environment=NODE_ENV=production` in the unit.

R6 — **`botSentIds` is in-memory only and unbounded** (`server.js:47,178,193`).
Growth is trivial at this volume (P3). Reset on restart means a re-delivered
own-message could be forwarded back to Hermes once (hunch — depends on
Baileys offline replay behavior). Minimal fix: cap the Set (~2 LOC);
persistence is not warranted.

R7 — **`writeNotify` non-atomic** (`server.js:66–68`): a crash mid-write
corrupts `notify.json`; `readNotify` then falls back to `{mode:"linked"}`
(`server.js:58–65`) — benign but silently reverts a typed-number
destination. Minimal fix: tmp+rename (3 LOC, the kb-admin
`atomicSave` pattern, `kb-admin/server.js:36–63`).

Not risks (verified OK): body limit is express's default 100 KB with Caddy
capping at 256 KB (`deploy/caddy/sites/support.caddy` `request_body`); alert
fields are bounded upstream (`processor/whatsapp_notifier.py:36–40,54–82`);
wrong-token probes get a plain 404 before auth (`server.js:291`); secrets
compared constant-time via SHA-256 + `timingSafeEqual`
(`whatsapp-connect/security.js:59–68`); heartbeat latches only on delivered
alerts so correlated failures retry (`processor/heartbeat.sh:89–120,166–173`).

## Simplification actions

| # | Action | Files | LOC Δ | Risk | Priority | Longevity gain |
|---|---|---|---|---|---|---|
| 1 | **LANDED (Wave 1, PR #28).** Reset `state` on non-loggedOut close (R2) — plus the R3 counter reset/reschedule discipline | `whatsapp-connect/server.js:138–141` | +1 | Very low | P1 | Truthful status for console + sendAlert guard |
| 2 | Bounded hold-and-retry in `/send` during reconnect windows (R1) | `whatsapp-connect/server.js:187–196,223–239` | +12 | Low | P1 | Closes the known ~2.5-min-flap alert-loss gap |
| 3 | **LANDED (Wave 1, PR #28).** Cap reconnect attempts → non-zero exit so systemd restarts (R3) — failure counter now resets only on `connection === "open"` and the failure path always reschedules, so the cap is reachable | `whatsapp-connect/server.js:137–141` | +5 | Low | P1 | No silent zombie alert path |
| 4 | Offline gate runs the two root test files, not just `test/` (guard on `node_modules`) | `tools/verify_release.sh:229` | +2 | Low | P2 | Gate matches CI's `npm test` discovery |
| 5 | Add `npm ci` (or documented VPS step) for whatsapp-connect deploys; verify qs 6.16.0 live (R4) | `deploy/cd/buttonsbebe-deploy-receive.sh` or ENV runbook | +3 | Low | P2 | Lock actually describes production |
| 6 | Decide bridge Hermes toolset posture: add the processor's `-t` read-only allow-list, or document that owner bridge sessions intentionally run cli toolsets (see below) | `whatsapp-connect/server.js:167–171`; `processor/hermes_runner/runner.py:35–56` | +1 | Medium (UX/safety decision) | P2 | Safety model symmetry, documented |
| 7 | Delete module-local systemd unit twin; point README at `deploy/` (W2b) | `whatsapp-connect/buttonsbebe-whatsapp-connect.service`, README | −18 | Low | P2 | One source of truth for units |
| 8 | One logging convention: use pino for all logs (or drop pino for a noop logger) + error middleware + `NODE_ENV=production` (W1a, R5) | `server.js:21,49–51,96`; systemd unit | ±10 | Low | P2 | Uniform, grep-able logs; no stack leaks |
| 9 | Unify `${BASE}/status` and `/wa/status` handlers; atomic `writeNotify` | `server.js:66–68,217–219,245–248` | −6 | Very low | P3 | Less drift between the two status APIs |
| 10 | Cap `botSentIds` (R6) | `server.js:47` | +2 | Very low | P3 | Bounded memory |
| 11 | Optional durable hardening: on-disk alert spool drained on reconnect | `server.js` | +35 | Medium | P3 | Survives service-down windows without processor retries |
| 12 | Owner decision: retire the public pairing page (W1b) — console Notifications tab already pairs; only if `WHATSAPP_SEND_URL` is confirmed localhost so `/send` can leave the public route | `server.js:202–209,217–219,289–291,299–429`; `deploy/caddy/sites/support.caddy:23–26`; systemd drop-in | −150 | Medium | P3 | Removes the last public console-bootstrap-adjacent surface + one secret |

Question answers folded in: (a) reboot → systemd `Restart=on-failure`/3 s +
multi-file auth means no re-pairing; socket drop → hand-rolled 2 s retry on
top of Baileys events with the R2/R3 gaps; loggedOut → creds wiped, fresh QR
(`server.js:123–137`) — good UX. (b) end-to-end: processor claim ledger +
single-attempt-by-design (`processor/orchestrator.py:570–597`), HTTP hop has
retries only on the non-durable path, bridge-internal hop has none (R1 fix).
(c) flat routing, default 100 KB body, `requireAuth`/`requireSendAuth` are
the only middleware, no error middleware (R5); 404 is hand-rolled only for
the token namespace (`server.js:291`) — deliberate, keeps the rest default.
(d) security.js covers credential extraction (Bearer or Basic-password),
constant-time compare, secret-strength/placeholder rejection, and
XFF-trust-only-from-loopback (`security.js:6–37,39–68`); the qs tests lock
the pinned qs against an array-limit-bypass parse bug and a
`constructor.isBuffer` prototype-pollution gadget — still live-relevant
because express parses every public token-path query string with qs even
though no route reads `req.query`; cheap defense-in-depth, but currently
enforced only in CI (see action 4). (e) the 2-way bridge runs
`execFile(HERMES_BIN, ["-z", text])` with **no `-t` toolsets**
(`server.js:167–171`) — the processor path enforces exactly the three
read-only toolsets (`processor/hermes_runner/runner.py:44–48`); the Hermes
config template gives the cli platform `file` + `terminal` toolsets
(`hermes/config.example.yaml:161–164`; live config is gitignored — hunch on
live behavior). No Gorgias toolset is attached, so there is **no Gorgias read
or write path** and no env credential loading (unit env is WA_* + HERMES_BIN
only; README: "Do not point the service at the shared application .env") —
safety model satisfied on the Gorgias axis, but bridge sessions are a
different, undocumented toolset posture than the processor's (action 6).
Owner-only self-chat filter verified: `server.js:152–154`. (f) `audit()` is
structured JSON but pino is installed yet only silences Baileys, and bare
string logs mix in — different style from processor's `log_event`. (g)
`startup-log.test.js` locks: loopback-only bind, exact startup line, and no
secret-bearing path in startup output — extracted via regex + VM
(`startup-log.test.js:5–15`); covered by CI `npm test`, missing from the
offline gate (action 4).

## Rejected on principle

- Twilio or any SMS/Cloud-API provider for escalation — release gate
  hard-fails on it; escalation stays on the local bridge.
- A queue package (bull/redis, sqlite job queue) for alert retries — new
  dependency + infra for a ~12-LOC bounded retry; the processor's
  claim/uncertain ledger already owns durability semantics.
- Rewriting whatsapp-connect to kb-admin's zero-dep style (or vice versa) —
  churn without payoff; document the two-convention split instead.
- Auto-retrying "uncertain" alerts from the processor — explicitly rejected
  upstream (`processor/orchestrator.py:565–569`: a timeout may follow an
  accepted send; no end-to-end idempotency) — keep.
- `express-basic-auth`-style dependency for the ~20-LOC tested fail-closed
  Basic/Bearer gate — far under the 100-line bar.
- Full systemd sandboxing (ProtectSystem etc.) on this unit now — the service
  must exec Hermes and write auth state; separate hardening track, not a
  simplification.

## Verification

- **Action 1 (state regression):** no existing test covers
  `connection.update` handling. Smallest new test: extract the close-branch
  state rule into a tiny function (e.g., `nextStateOnClose(code)` beside
  `security.js`) and assert `loggedOut → "qr"`, other codes → `"connecting"`
  in `whatsapp-connect/test/security.test.js` style (~20 LOC).
- **Action 2 (hold-and-retry):** smallest new test: export the retrying send
  helper and pass a fake sock whose `sendMessage` rejects once then resolves;
  assert the alert still resolves (~25 LOC, mirrors the fake-response
  pattern already in `test/security.test.js:29–52`).
- **Action 3 (retry cap):** same extracted function as action 1 can assert
  "exit non-zero after N consecutive startSock failures" via a counter;
  alternatively verify by `bash tools/verify_release.sh` staying green.
- **Actions 4 (gate glob):** existing coverage is CI's `npm ci && npm test`
  (`.github/workflows/ci.yml:59–70`) which discovers all three test files;
  after the edit, run `bash tools/verify_release.sh` — with a
  `node_modules` guard so the gate stays runnable on dep-only machines.
- **Action 5 (npm ci):** existing test: `deploy/tests/test_caddy_config.py:176`
  is the pattern for pinning deploy expectations; verification is the
  `/wa/status` version echo (or one `journalctl` check documented in the ENV
  runbook).
- **Action 7 (unit deletion):** verification is grep-based —
  `deploy/tests/test_caddy_config.py:176` already asserts the retired
  module-local Caddyfile; the same assertion style pins the unit's absence.
- **Actions 8–10:** existing tests `test/security.test.js:29–52`
  (middleware fail-closed) and `startup-log.test.js` (loopback bind) already
  guard the two behaviors most likely to regress during the logging/middleware
  touch; rerun `npm test` locally after a fresh `npm ci` (the current local
  failures are the stale `node_modules`, not the code — verified installed
  qs 6.15.3 vs locked 6.16.0).
