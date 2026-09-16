# 07 — Console SPA (`console-src/index.html`, `login.html`, `support-theme.css`)

Analyst: module 7 of 13 (console SPA). Read-only analysis; this file is the only output.
Source of truth: `AGENTS.md` (§2 safety model, §5 components, §6 Caddy). Background:
`DESIGN-CRITIQUE.md` (its B3 stale-ticket and escaping claims are cited below, not re-derived).

## Snapshot

| File | Lines | Bytes | Notes |
|---|---|---|---|
| `console-src/index.html` | 1,017 | 211 KB | The console SPA. ~638 JS lines (63%), ~367 CSS lines, 2 body lines. **~94 KB (44%) is generated base64** (3 Jost fonts + logo, `index.html:286-289`). Hand-written JS+CSS ≈ 810 lines. |
| `console-src/login.html` | 235 | 115 KB | Login page. ~90 KB generated brand block; ~20 lines hand markup; ~7 lines JS. No `innerHTML` anywhere (errors via `textContent`, `login.html:221`). |
| `console-src/support-theme.css` | 102 | 8.7 KB | Canonical source of the shared theme block; embedded byte-identically into `index.html:180-283`, `login.html:16-119`, `inbox/index.html:11` by `tools/build_support_theme.py`. |
| `console-src/brand/` | 6 files | ~60 KB | Jost TTFs, `logo.svg`, `brand.css` (7 KB), OFL. Consumed only by `tools/sync_console_brand.py`. |
| `console-src/test/` | 3 files, 198 | — | `action-operations.test.js` (88), `ops-health.test.js` (71), `connections.test.js` (39). All run in the gate: `tools/verify_release.sh:230`. |

**Interface/seam.** The SPA is a single HTML file deployed to the web root by CD
(`deploy/cd/source_release.py:126-129` ships exactly `index.html` + `login.html` to `web/`).
It consumes three proxied API namespaces — `/console/api/*` → `:8000 /dashboard/api/*`
(`deploy/caddy/sites/support.caddy:82-93`), `/console/kbapi` → kb-admin `:8087`
(`support.caddy:108-117`), `/console/waapi` → whatsapp-connect `:8085`
(`support.caddy:95-106`) — all behind `forward_auth` with a signed HttpOnly session cookie
(`webhook/src/bb_webhook/routers/auth.py:115-125`). Login at `/console/login`.

**Job description.** The console is the human safety gate's cockpit: it lists processed
tickets with their AI drafts, lets the human edit / send (with confirm) / note / request
rewrite, runs the Notice Board (owner overrides via kb-admin), surfaces system health,
notifications, and WhatsApp link status. Sends must be idempotent and never silently
double-fire; everything customer-supplied must render escaped.

**Scale/shape verdict (question a).** The single-file choice is **deliberate zero-build
design, not rot**. Evidence: (1) CD ships exactly two files, no asset routes
(`source_release.py:126-129`); (2) both shared CSS layers are generated into the pages by
tools with `--check` modes wired into the release gate (`verify_release.sh:78,190`), with
docstrings stating the intent ("Embed the shared theme in pages so existing single-file
console CD works", `tools/build_support_theme.py:1`; "…without new public asset routes",
`tools/sync_console_brand.py:1`); (3) the hand-written JS has real section discipline:
state (379-396) → icons (397-403) → helpers (404-413) → ops card (414-455) → shell
render/bind (456-523) → overview (524-565) → tickets (566-626) → action safety
(627-705) → conns (706-729) → KB (730-793) → settings (744-753) → notifications/WA
(794-905) → notices (906-976) → event wiring (977-1013). Functions are ordered by feature
and every view is a pure template function over module state. **Deletion test result:
no concrete maintainability failure justifies a split** — 638 well-ordered lines with one
`innerHTML` sink is easier to audit than fragments would be. No split proposed.

## Reinvented wheels

Honest result: this module is *light* on reinvented wheels. The two big historical
duplication risks (theme, brand) are already solved by build-time sync tooling with CI
`--check` enforcement — **the existing tools are sufficient** (see Duplication map).

| # | Hand-rolled | Existing wheel | Evidence file:line | LOC saved | Switch risk |
|---|---|---|---|---|---|
| W1-1 | Per-digit KPI number animation (`digits()` + `.t-digit` spans + stagger attrs) | CSS `@keyframes` on the whole element (the file already does this for `.spin`, `index.html:144-145`) | `index.html:409`, `170-171` | ~8 | Visual: loses per-digit stagger polish. Deliberate design, not accidental — assessed, kept. |
| W1-2 | `ago()` relative time | `Intl.RelativeTimeFormat` (used elsewhere in the file via `Intl.DateTimeFormat`, `index.html:909`) | `index.html:408` | ~2 | Output wording changes; not worth it. Assessed, kept. |
| W1-3 | `hashActionText` hex-dump of a digest | No platform one-liner exists; `crypto.subtle.digest` is already used correctly | `index.html:632-633` | 0 | — (correct platform use, not a violation) |
| W3-1 | Notification poll: self-re-arming `setTimeout` chain at 15 s | Native polling doesn't exist; SSE/WebSocket would be the wheel but requires server work | `index.html:858-864` | 0 | Rejected (see Rejected on principle) |

Non-findings worth recording: `jget()` (`index.html:412`) is a 1-line config wrapper
(cache-bust), not a reinvented fetch; `esc()` (`index.html:404`) is the standard escape
idiom (no DOM built-in does this better); `confirm()`/`prompt()` for send/note/unlink/new-KB-file
(`index.html:665,778,902`) are platform natives, boring and appropriate for a
single-operator console; `crypto.randomUUID` used for operation ids (`index.html:640`).
The localStorage send-idempotency layer (`index.html:631-655`) is justified *app* logic
(not a platform wheel) and is the module's most-tested code (`console-src/test/action-operations.test.js`).

A new framework/bundler/npm dependency: **rejected on principle** — see final section.

## Duplication map

**Within module — all three copies verified byte-identical (managed, not rot):**

| What | Copies | Managed by | Drift guard |
|---|---|---|---|
| `support-theme.css` (102 lines) | inline in `index.html:180-283`, `login.html:16-119`, `inbox/index.html:11+` | `tools/build_support_theme.py` | `--check` in gate (`verify_release.sh:190`) |
| Brand block (fonts+logo+brand.css, ~94 KB) | inline in `index.html:285-373`, `login.html:121+`; template in `whatsapp-connect/server.js` | `tools/sync_console_brand.py` | `--check` in gate (`verify_release.sh:78`) |

Both tools are idempotent, refuse duplicate blocks, and fail the gate on staleness.
**Assessment: sufficient.** The one caveat: hand-editing the *inline* copies is possible
locally until the gate runs — the generated headers ("Generated by …; edit
console-src/brand instead", `index.html:285`) mitigate this.

**Notable cost, not a defect:** the shared `support-theme` block carries ~60 lines of
`body[data-support-page=console]` rules that are inert inside `login.html` (login needs
only the `:root` tokens + `[data-support-page=login]` rules). This is the price of
one-source-embedded-everywhere; bytes only, no drift risk.

**Layering, in-page:** the live console page stacks three theme layers — base
`<style>` (`index.html:7-148`), `support-theme` (180-283), `buttonsbebe-brand` (285-373)
— each re-declaring overlapping tokens (`--acc`, `--bg`, `--line`, `--radius`, greens…).
Final-wins cascade means only the brand layer's values are live at runtime
(e.g. `--radius: 12px` at `index.html:291` beats support-theme's `6px` at `index.html:188`).
All three layers do real work (base = component layout, support-theme = mobile drawer +
neutral skin, brand = identity), so no clean deletion exists — but predicting any style
requires reading three layers. P3 note below.

**Vs other modules:**

| Duplication | Other path | Quantified | Proposed home |
|---|---|---|---|
| `esc()` HTML-escape helper | `console-src/inbox/js/util.js:1-8` | 8 lines, near-identical | None now: SPA and inbox deploy to different roots (`web/` vs `inbox/`, `deploy/cd/README.md:16-19`); a shared JS file needs a new public route or a third sync tool — overkill for 8 lines. Fold into `build_support_theme.py`-style sync only if more shared JS appears. |
| Design-token palette `:root` | `console-src/inbox/styles.css:1-15` | ~12 lines of same hex values (`#F4F0EA`, `#FFFDF9`, `#1C1916`, `#5C564F`, `#B5471D`, IBM Plex) — **and one real inconsistency: `--line` is `rgba(28,25,22,.08)` in `styles.css:6` but `rgba(28,25,22,.12)` in `support-theme.css:5`; the later inline block silently wins on the inbox page** | Delete the overlapping declarations from `inbox/styles.css` `:root` (keep its unique `--ok`/`--warn` tokens); the synced support-theme block already overrides them in-page. P3. |
| Client-side `nextPath()` open-redirect guard | `webhook/src/bb_webhook/console_auth.py:152-174` (`safe_next_path`) | ~3 lines duplicated policy | Keep: server re-validates authoritatively at login (`auth.py:107`) and page-check (`auth.py:160`); the client copy only avoids a wasted round trip. Intentional double validation, no action. |

## Reliability risks

**R1 — XSS surface (question b). Top finding; state of affairs is good, the gap is
enforcement.** There is **one central escape helper** — `esc()` at `index.html:404`
(`& < > " '` — covers element content and double-quoted attributes) — used at 35 call
sites, and exactly two DOM sinks: the full-app render (`document.getElementById("app").innerHTML`,
`index.html:484`) and the ops card patch (`card.innerHTML=opsCardContents()`,
`index.html:442`). I enumerated every `${…}` interpolation not wrapped in `esc()` and
traced each: all resolve to static lookup tables (`${pages[tab]}`/`${subs[tab]}`,
`index.html:494`), static filter keys (`data-go-filter="${k.f}"`, 546; `data-f="${f[0]}"`,
571), static risk labels (549), integers (`${index}`), numbers passed through `digits()`
which escapes each character itself (`esc(ch)`, `index.html:409`), or the escaped
`health.issues.map(esc)` (437). Every customer-controlled field is escaped at render:
subject/email/name/status/draft/reason/message (`row()`, `index.html:597-621`),
notification title/context/detail (`index.html:833-835`), notice text/id/when/who
(`noticeCard()`, `index.html:922-928`), KB folder/path/title (`index.html:732`).
`login.html` has zero sinks (errors via `textContent`, `login.html:221`).
Two residual notes: (1) `${waData.qr}` lands in an `img src` unescaped (`index.html:805`)
— it's a server-minted data-URI from the operator's own whatsapp-connect service, so
trusted-origin, but it is the one interpolation where a non-`esc`'d server string meets
an attribute context; label: low risk, cited for completeness. (2) The ops card already
has a hostile-input regression test (`ops-health.test.js`, `checks:{private:'<img src=x
onerror=alert(1)>'}` → asserts no `onerror` in output) — but **the ticket row, notice card,
and notification row have no equivalent render test**. The escaping is manual discipline;
nothing automated catches a future contributor writing `${t.customer_name}` bare.
DESIGN-CRITIQUE.md §1 ("All user text is escaped", written against the Fable console)
remains true of this SPA, but with the same caveat: discipline, not enforcement.

**R2 — Session expiry mid-session is swallowed as a data error (question c).**
`jget()` returns `null` on any non-OK response including 401 (`index.html:412`); `boot()`
turns that into "Could not load tickets data; showing last known values"
(`index.html:466-470,476`) and the retry button re-runs the same 401-ing calls
(`index.html:508`). The only 401-aware surface is the ops card, which special-cases
`response.status===401` into a "Sign in to view system health" state with a login link
(`index.html:447`, tested in `ops-health.test.js`). **Scenario: session expires while the
tab sits open → every refresh shows a generic load-failure banner → the human keeps
reviewing and can still act on stale drafts believing they're live.** (Initial load is
safe — Caddy `forward_auth` page-check redirects to login, `support.caddy:126-129`,
`auth.py:150-165`.) Minimal fix: make `jget`/`boot` distinguish 401 (a `sessionExpired`
flag → error banner renders "Your session needs renewal — Sign in again" link, reusing
the ops-card pattern). ~8 app LOC.

**R3 — Stale-draft guard compares against the same stale snapshot.** `submitAction`
refuses to send if `actSourceDraft!==(t.draft_text||"")` (`index.html:661`), but `t`
comes from the in-memory `tickets` array loaded at boot — **the SPA has no ticket
polling at all** (only ops 60 s, notifications 15 s, reindex 3 s; tickets refresh solely
on manual refresh/retry, `index.html:462-476,508`). So the client guard can only catch
draft changes the local array already knows about; the authoritative staleness gate must
be the server-side `draft_revision` hash sent with every action (`index.html:669`).
DESIGN-CRITIQUE.md B3 flagged the sibling risk on the Fable inbox. Cross-module hunch
(the webhook router is another analyst's module): the server *does* enforce it —
`webhook/test_console_actions_require_text.py` and the `draft_revision` field exist;
verify there rather than adding client polling. No client change recommended.

**R4 — Polling hygiene: no backoff, but bounded and correct.** On persistent failure the
15 s notification chain self-re-arms forever (no error backoff; each tick costs 1-2 light
GETs, `index.html:858-864`); ops polls at 60 s with a `document.hidden` guard and an
`opsBusy` re-entrancy guard plus a 5 s `AbortController` timeout (`index.html:443-454`);
`pollReindex` chains at 3 s only while the server reports running (`index.html:786-791`).
No timer leaks found (notification poll cleared before re-arm, `index.html:860`;
`opsPoll` guarded `if(!opsPoll)`). Consequence of no backoff is marginal (a dead session
or dead server produces ~8 req/min of failing GETs from one operator tab). Minimal fix,
optional: double the notification interval after a failure, reset on success (~3 LOC).
P3.

**R5 — Send-path failure states: strong, tested.** `submitAction` uses
confirm → localStorage operation id (`crypto.randomUUID`) → POST with
`{text,confirmed:true,operation_id,source_message_id,draft_revision,approve_learning}`
(`index.html:657-677`); unresolved previous action blocks different-text resubmission
(`actionOperation` throw, `index.html:638`); async completions are generation-guarded so
a late response can't clobber a newly opened editor (`index.html:677,695`; covered by
`action-operations.test.js` tests "switching tickets during operation preparation…",
"status completion cannot overwrite…"). Network failure mid-send surfaces "Delivery
could not be confirmed…" and the check-status flow resolves it (`index.html:679-695`).
No fix needed — recorded because it's the safety-critical path.

## Simplification actions

| # | Action | Files | LOC Δ | Risk | Priority | Longevity gain |
|---|---|---|---|---|---|---|
| 1 | Distinguish 401 in `jget`/`boot`; render a "session needs renewal — Sign in again" banner (reuse ops-card pattern, `index.html:438,447`) | `console-src/index.html` | +8 | Low (display-only; initial-load redirect unaffected) | **P1** | Human gate never silently reviews stale data |
| 2 | Hostile-input render test for `row()`, `noticeCard()`, notification rows — mirror the existing ops-card XSS test pattern (`console-src/test/ops-health.test.js`), asserting no raw `<`/`onerror` from hostile subject/email/notice text | `console-src/test/` (new ~25-line test file, string-slice pattern) | +25 test | None (test-only) | **P1** | Converts escaping discipline into an enforced invariant |
| 3 | Delete retired `dashboard/` (see Verification for steps) | `dashboard/` (622+154 lines) | **-776** | None at runtime: not deployed (web root ships only `console-src/index.html`+`login.html`, `deploy/cd/source_release.py:126-129`); no Caddy/CD/gate/test references (grep verified) | **P2** | Removes the "which console is real?" trap; RETIRED.md row satisfied |
| 4 | Tidy `.github/workflows/pr-review.yml:46` label matcher (`"Dashboard", [/^(?:console-src|dashboard)\//]`) when #3 lands | `.github/workflows/pr-review.yml` | -1 | None (matcher becomes no-op otherwise) | P2 | Keeps CI config truthful |
| 5 | Delete dead CSS in the base style block: `.logo .mark` (14), `.logo b` (15), `.step .ck` (96), `.toggle`/`.sw` (98-100), `.acctstrip`/`.acct` (121-123) — rendered markup uses `.bb-logo`, `.ic`, and never these (grep-verified; ops-health test even asserts markup avoids `class="ck"`, `ops-health.test.js:70`) | `console-src/index.html` | -10 | Low (visual check on one preview) | P3 | Smaller hand-written layer |
| 6 | Dedupe `:root` palette in inbox preview stylesheet (overlapping tokens are overridden in-page anyway; **fixes the silent `--line` .08 vs .12 divergence**, `inbox/styles.css:6` vs `support-theme.css:5`) | `console-src/inbox/styles.css` | -12 | Low (preview app only; verify in `run-review.sh` preview) | P3 | One palette source of truth |
| 7 | Comment documenting the CSS layer order (base → support-theme → brand) at the top of the base `<style>` | `console-src/index.html` | +2 | None | P3 | Future editors stop re-deriving the cascade |

No action on the single-file structure, the two sync tools, `esc()` duplication, or the
polling loops — see Rejected / Duplication map for the reasoning.

## Rejected on principle

- **Framework or bundler (React/Vue/Vite/esbuild):** rejected — this is a deliberate
  zero-build, two-file deploy (`deploy/cd/source_release.py:126-129`); a build step would
  add infrastructure to ship two static files, and boring vanilla outlives frameworks.
- **New npm dependency** (e.g. DOMPurify, a fetch lib): rejected — no `package.json`
  exists for the console; `esc()` + `textContent` + native `fetch`/`confirm` are
  auditable and sufficient; the gate runs the tests with plain `node --test`
  (`verify_release.sh:230`).
- **Splitting JS into `<script src>` files / CSS into stylesheets:** rejected for now —
  it would require new public file routes in Caddy + CD manifest changes; the 638-line
  JS block has working section discipline and one sink (question (a) deletion test
  passed). Revisit only if a concrete locality failure appears.
- **SSE/WebSocket for live updates:** rejected — server work for a single-operator
  console where 15-60 s cadence plus manual refresh is fine (and ticket polling is a
  product decision, not a simplification).
- **Moving the WhatsApp QR or notices to a richer client state machine:** rejected —
  the `waSig` string-diff re-render trick (`index.html:470,851-856`) is small, works,
  and is the kind of boring code this project should keep.

## Verification

**P1-1 (401 surfacing).** No existing test covers `jget` 401 behavior (ops-card 401 *is*
covered, `ops-health.test.js`). Smallest new test: string-slice `jget` + the banner
branch, vm-harness with `fetch` returning `{status:401,ok:false}` → assert the rendered
message contains "session"/"Sign in" and a `/console/login` link (same harness shape as
`ops-health.test.js:12-22`). Manual: expire the session cookie in devtools, click
Refresh → banner must offer sign-in.

**P1-2 (XSS render guard).** Smallest new test: slice `row()` + `esc` + helpers
(`nameOf`, `keyOf`, `isEsc`, `ago`, `cleanMessage`, `cleanDraft`, `digits`) into a vm
context (the `action-operations.test.js:8-14` browser/slice pattern), feed a ticket with
`ticket_subject:"<img src=x onerror=alert(1)>"`, `customer_email:"a@b<script>"`,
`message_text:"<svg onload=…>"`, and a notice with `text:"<b>hi</b>"`; assert output
contains `&lt;img` and no raw `onerror=`. ~25 lines, zero new tooling.

**P2-3 (delete `dashboard/`).** Steps with verification: (1) confirm no live refs —
`rg -n "dashboard/" deploy/ tools/ .github/ webhook/ processor/ testing/` excluding
`/dashboard/api` hits (done during analysis: only `pr-review.yml:46` matches);
(2) `git rm -r dashboard/`; (3) edit `pr-review.yml:46` to drop `dashboard|` from the
matcher; (4) update `AGENTS.md` §5 (`dashboard/index.html` row) and `RETIRED.md`
(`dashboard/` row); (5) run `bash tools/verify_release.sh` (gate already ignores the
dir — it never syntax-checked or shipped it) and push; CI auto-deploy serves the console
from `console-src` as before (`source_release.py:126-129`). Rollback = `git revert`.

**P2-4.** CI green on the same PR as #3 (matcher change is YAML-only).

**P3-5/6/7.** #5/#7: open the deployed console in a browser (or the preview) and diff
screenshots of Overview/Tickets/Settings — layout identical (only unused selectors
removed); existing `console-src/test/*.test.js` must stay green (the `class="ck"`
absence assertion at `ops-health.test.js:70` is unaffected). #6: `console-src/inbox/run-review.sh`
preview (`http://127.0.0.1:8766/`, per AGENTS.md §Workspace facts) — visually confirm
rail/list borders before/after; the `--line` token value should *stay* the .12 the page
already effectively used.
