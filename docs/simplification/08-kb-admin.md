# 08 — kb-admin (KB editor API)

Analyst scope: `kb-admin/` — the zero-dependency Node API on :8087 that backs
the console's KB screens via Caddy `/console/kbapi/*`. Read-only analysis; no
code changed.

## Snapshot

| File | LOC (`wc -l`) |
|---|---|
| `kb-admin/server.js` | 329 |
| `kb-admin/buttonsbebe-kb-admin.service` | 15 |
| `kb-admin/test/server.test.js` | 230 |
| `kb-admin/package.json` | *(does not exist — service is deliberately zero-dependency)* |

**Interface/seam:** binds `127.0.0.1:8087` (`server.js:329`); Caddy's
`handle_path /console/kbapi/*` gates it behind `forward_auth` (session cookie)
and proxies to :8087 (`deploy/caddy/sites/support.caddy:108-115`). It reads and
writes KB markdown under `KB_DIR` (default `/root/Buttonsbebe Agent/KB`,
`server.js:14`), restricted to the writable folders `intents/ faq/ policies/
tickets/` (`server.js:15`); `products/` (VPS-generated) and `learned/`
(PII-gated) are structurally excluded — the allowlist is the security
invariant. It also owns the Notice Board JSON (`notices/notices.json`), a
shared contract with `kb/scripts/notices_lib.py:1-15`. Reindex shells out to
`KB/update.sh` (which runs `scripts/index_kb.py`, `kb/update.sh:1-4`).

**Job (2-3 sentences):** a single-file HTTP API that lets the authenticated
console list, read, edit, and create KB markdown files, trigger a reindex, and
post/remove owner-override notices. All routing, body parsing, path
validation, atomic save, and locking are hand-rolled on Node stdlib. The
release gate syntax-checks it and runs its 8-test suite
(`tools/verify_release.sh:231-232`).

## Reinvented wheels

| # | Hand-rolled | Existing wheel | Evidence | LOC saved | Switch risk |
|---|---|---|---|---|---|
| W1 | Entire HTTP layer: routing if-chain, `readBody` (size cap 1 MB, 10 s timeout, JSON validation), `send()` JSON helper | Express (already a dep in `whatsapp-connect/package.json:12`) or Node stdlib patterns | `server.js:71-90,205-327` vs `whatsapp-connect/server.js:17,199-200` (`app.use(express.json())`) | ~60-80 net after swap | **High — see verdict below; recommend NO switch.** |

**W1 verdict — keep zero-dependency (no fence-sitting):** for ~1.6k equivalent
hand-rolled characters across a 329-line file, the zero-dep choice IS the
reliability feature. The service is tiny, its whole attack surface is one file
whose every line is auditable, it has zero patch churn for 10 years, and it
already handles the hard parts (body limits, timeouts, JSON shape validation)
that `express.json()` also handles — but with `express` we would inherit the
supply-chain surface of ~60 transitive packages and CVE patching duty on a box
whose console security model says "no writes without a human." The one place
Express would genuinely beat the hand-rolled code is malformed-request-line
handling — and that is a 3-line fix (see R1), not a framework migration.
Verdict: **the wheel is hand-rolled correctly enough that switching is a net
reliability loss.** Note the asymmetry honestly: `whatsapp-connect` (429
LOC, already on Express) made the opposite call per-module, which is
defensible for a Baileys app with heavier routing; kb-admin's routing is 9
routes, not worth a framework.

| # | Hand-rolled | Existing wheel | Evidence | LOC saved | Switch risk |
|---|---|---|---|---|---|
| W2 | `send(res, code, obj)` JSON responder — re-implemented in whatsapp-connect (different shape) and the webhook console (FastAPI) | One shared tiny `send` is possible but cross-module stdlib modules don't exist in this repo's layout; low value | `server.js:71-74` | ~5 | Low value; skip |
| W3 | `readBody` JSON body parser (size cap, timeout, error states) | `express.json()` does this; also partially duplicated logic conceptually in whatsapp-connect's Express usage | `server.js:75-90` | ~15 | Same verdict as W1: keep. The 1 MB / 10 s bounds (server.js:77-79) exceed express defaults' safety only in explicitness, not behavior |

**W3 (app re-doing platform features):** essentially clean. Caddy owns auth,
TLS, public routing, and the 404 fallback — the app adds no auth layer of its
own (correct, see (e)); it does NOT serve static files, do redirects, or
re-implement logging middleware. The only platform-adjacent overlap is the
app re-implementing its own "service health" (`/health`, `server.js:209-212`)
rather than leaving liveness to systemd — that is a deliberate, documented
console feature (product freshness), not a reinvented wheel.

## Duplication map

**Within module:**

1. **Two copies of the systemd unit.** `kb-admin/buttonsbebe-kb-admin.service`
   and `deploy/systemd/buttonsbebe-kb-admin.service` differ only in the
   `__NODE__` placeholder vs `/usr/local/bin/node`
   (`kb-admin/...service:10` vs `deploy/systemd/...service:10`; verified by
   diff). CD gates `deploy/systemd` by fingerprint
   (`deploy/cd/buttonsbebe-deploy-receive.sh:112`); the `kb-admin/` copy is
   not the deploy source and can silently drift (it already drifted in
   ExecStart).
2. **`safePath` re-validation inside `atomicSave`** (`server.js:41,50`): a
   deliberate TOCTOU re-check, not harmful duplication — but it means the
   path-validation rules live in one place, which is good.
3. **Notice lock + atomic JSON write duplicated cross-language** — this is
   *by design* (shared file contract, `notices_lib.py:6-7`), but the Node
   side re-implements: lock-dir acquire/stale-break/release
   (`server.js:167-188`) ≈ `notices_lib.py:87-111`; tmp+rename JSON write
   (`server.js:189-198`) ≈ `notices_lib.py:122-137` (`os.replace`); schema
   validation `validNotice` (`server.js:147-152`) ≈ `_valid_notice`
   (`notices_lib.py:66-73`). Semantics currently match (verified: same 60 s
   stale window — `server.js:19` vs `notices_lib.py:36`; same lock dir name;
   same strict/non-strict read pair). The Python side does fsync the temp
   before rename (`notices_lib.py:131`) — the Node side does NOT
   (`server.js:193`). Named for the KB-side analyst: the shared convention
   should be recorded in `kb/CONVENTIONS.md` if it isn't; any divergence is a
   cross-language corruption risk.
4. **Dead code: `loadNotices()`** (`server.js:160-163`) is defined but never
   called — all four callers use `loadNoticesStrict()`. ~4 LOC deletion.

**Vs other modules:**

- **`whatsapp-connect/server.js`**: both are Node HTTP services behind Caddy
  forward_auth. Auth glue: neither app validates the session itself — both
  trust Caddy (correct, consistent). Body parsing: whatsapp-connect uses
  `express.json()` (`server.js:200`), kb-admin hand-rolls — divergence noted
  in W1, verdict: keep as-is. No shared Node module exists between them;
  creating one for ~2 functions would be over-engineering (rejected below).
- **Webhook console API (FastAPI)**: notice post/delete also exists there?
  Verified NO — console posts notices exclusively via `KBAPI+"/notices"`
  (`console-src/index.html:958,969`), single owner of the write path. Good.
- **`console-src/index.html` test coverage of kb-admin contract** —
  surprisingly, `kb-admin/test/server.test.js:147-189` asserts on the
  console's HTML/JS strings (30+ regex assertions). This couples the kb-admin
  test suite to the console UI file; arguably belongs in
  `console-src/test/`, but it exists because the release gate groups them;
  moving is cosmetic, low value. (Note: `console-src/test/` has 3 suites; the
  gate runs both `console-src/test/*.test.js` and `kb-admin/test/*.test.js`,
  `tools/verify_release.sh:230-232`.)

## Reliability risks

Each: scenario → consequence → minimal fix (evidence).

**R1 — Process crash on malformed request line (P1, security/reliability).**
Scenario: any client that can reach :8087 sends `GET // HTTP/1.1` (or any
req.url that is an invalid WHATWG URL: `//`, `http://`, `*` variants). Line
`const u = new URL(req.url, "http://x")` (`server.js:206`) throws
`TypeError: ERR_INVALID_URL` outside any try/catch. Consequence: **uncaught
exception → the whole kb-admin process dies** (verified empirically: a
minimal repro with the same handler line exits the Node process with the
stack shown; Node does not catch handler exceptions). systemd `Restart=on-
failure` (`...service:11`) restarts it in ~3 s, but during that window every
console KB request 502s, and a trivial loop from any co-located process
keeps the service down. Reachability: localhost-only + Caddy forward_auth
means the *public* attacker can't send this (Caddy normalizes `//` before
proxying — labeled hunch, unverified; but `whatsapp-connect`, any local
process, or a misconfigured future Caddy rule can). Minimal fix: wrap the
handler's first line in try/catch → 400 (3 LOC), or set
`server.on("close"...)`/a top-level `process.on("uncaughtException")` guard
that responds 400. One-line version: `let u; try { u = new URL(req.url,
"http://x"); } catch { return send(res, 400, {error:"bad url"}); }`.

**R2 — Unbounded `.bak-*` accumulation in KB folders (P2).** Scenario: the
owner edits one policy file daily via the console; each save creates
`file.md.bak-<ts>-<rand>` (`server.js:52-53`) and nothing ever deletes or
prunes backups. Consequence: (a) `contentFiles()` filters them out of the
console list only by accident of extension — `!n.endsWith(".md")`?
Verified: filter is `n.endsWith(".md")` at `server.js:96`, so `foo.md.bak-…`
fails the `.md` suffix test — BUT the *indexer* `kb/scripts/kb_lib.py:133`
scans `rglob("*.md")` and skips only names starting with `_`/`.`; `foo.md.bak-
1694…` does not match `*.md` either (verified glob semantics), so indexing is
safe. The real consequence is (b) unbounded disk growth with no GC and (c)
backups land *inside the editable content folders*, where the nightly
promotion and future tooling don't expect foreign files. Minimal fix: write
backups into `KB/.backups/` (dot-prefixed → invisible to indexer and
`contentFiles`), and keep only the last N per file (or rely on git — but
these are VPS-side runtime edits, not in git, so backups ARE the recovery
path; a 20-deep ring is enough). ~10 LOC change in `atomicSave`.

**R3 — `/reindex` child stdout/stderr pipes can fill (P2).** Scenario: owner
clicks reindex; `spawn("/bin/bash", [KB/update.sh], { cwd: KB })`
(`server.js:261`) with default stdio (pipe) — but no `data` listener is ever
attached to `ch.stdout`/`ch.stderr`. Consequence: if `index_kb.py` prints
more than the ~64 KB pipe buffer (it does print progress lines,
`kb/scripts/index_kb.py:145,146,168`; a big first-run model download or large
corpus can exceed it), the child blocks on write forever; `reindex.running`
stays `true` (`server.js:262`), the console shows "reindexing" indefinitely
(console polls `/reindex-status`, `console-src/index.html:788`), and every
subsequent `/reindex` returns `{started:false}` (`server.js:259`) until the
service is manually restarted. Minimal fix: `spawn(..., { cwd: KB, stdio:
["ignore","ignore","ignore"] })` or attach `.on("data", ()=>{})` drains — 1
argument change. (Hunch component: I did not measure index_kb.py's total
output; but no-drain is a latent deadlock regardless of current volume.)

**R4 — No log of who saved what (P2, observability).** Scenario: a wrong
policy edit ships; the owner asks "who changed shipping.md and when?"
Consequence: the API responds only `{ok:true, path}` (`server.js:253`) and
the service prints nothing (only startup line, `server.js:329`). The
`.bak-*` files carry no author info and console actions are not in the
learning ledger for KB *edits* (learning loop covers ticket actions, per
AGENTS.md §11). Recovery depends on file mtimes. Minimal fix: `console.log`
a one-line JSON audit (path, size, ts) to journald per save — ~3 LOC.
systemd already captures stdout to journald. (The unit's environment could
carry `JOURNAL_STREAM`; not needed for plain console.log.)

**R5 — `/new` normalizes filename but `save` accepts what console sends
(documented behavior, low risk).** `POST /new` slugifies
(`server.js:242-244`); `POST /save` accepts any
`[A-Za-z0-9._-]+\.md` name (`server.js:31`). Not a bug — the strict charset
already blocks injection — but note the asymmetry: files created via `/save`
can carry names `/new` would have rewritten. Consequence: cosmetic
inconsistency only; both go through the same `safePath`. No fix needed
(rejected as churn).

**R6 — Concurrent editor vs nightly promotion on the same file (labeled
hunch, structural, P3).** kb-admin's `atomicSave` is atomic for *readers*
(tmp+rename/link, `server.js:57-58`), so a concurrently-running indexer never
sees a half file. But there is **no cross-process lock between a console save
and the nightly `auto_promote_learned.py`/`index_kb.py` run**: a save landing
between the nightly's read and its index build means the built index reflects
the pre-edit content and the just-saved content is unindexed until the next
manual reindex — the console's own "Saved — re-index to apply" message
(`console-src/index.html:773`) shows this is the accepted workflow. Nightly
writes only create `exemplar-learned-*.md` (`auto_promote_learned.py:142`)
with a deterministic-name collision check (verified `FileExistsError` on
conflict, `auto_promote_learned.py:79`), so it can't clobber a console-edited
file of the same name — it fails loudly instead. **Overlap with the kb/
analyst:** kb-admin uses NO lock for markdown saves while `index_kb.py` uses
`flock` on `.index_kb.lock`/`.index_kb.promote.lock`
(`kb/scripts/index_kb.py:24,40-65`); the notices use a lock-dir. Proposed
shared convention to record in `kb/CONVENTIONS.md`: "console saves are
lock-free single-file atomic renames; indexers take flock; notice writers
take the lock-dir; nobody waits on another's lock." Current behavior is
safe-but-stale-until-next-reindex; document rather than add a lock (adding a
shared markdown lock would couple the console's save latency to a potentially
long index build — a bad trade).

**R7 — `/health` does a full directory scan of products/ on every console
refresh (P3, efficiency).** `kbHealth()` stats every product file
(`server.js:113-126`) and the console calls it on every polling loop
alongside 5 other endpoints (`console-src/index.html:464`). Consequence: with
a large product corpus this makes every refresh do N stat() calls; currently
fine at ~4k products (AGENTS.md mentions ~4,246 in tests) — measurable but
not urgent. Minimal fix: cache for 60 s (`server.js:103-144`), ~5 LOC. Label:
efficiency only; no correctness issue.

## Simplification actions

| # | Action | Files | LOC Δ | Risk | Priority | Longevity gain |
|---|---|---|---|---|---|---|
| 1 | Wrap `new URL(req.url, base)` in try/catch → 400 (R1) | `kb-admin/server.js:206` | +3 | Tiny — pure error-path addition, gated by existing suite | **P1** | Closes a trivially-triggered process-death vector on a 10-year service |
| 2 | Drain or detach reindex child stdio (R3) | `kb-admin/server.js:261` | ±0 (one spawn option) | Tiny | **P1** | Removes a latent deadlock that shows up as "reindex stuck forever" with zero diagnostics |
| 3 | Delete dead `loadNotices()` | `kb-admin/server.js:160-163` | −4 | Zero — provably unreferenced (only `loadNoticesStrict` is called, `server.js:272,299,311`) | P2 | Removes a confusing near-duplicate of `loadNoticesStrict` (a future maintainer might "simplify" by calling it and silently drop the strict corruption check) |
| 4 | Move save backups out of content folders into `KB/.backups/` + cap retention (R2) | `kb-admin/server.js:52-58` | ~+8/−2 | Low — indexer ignores dot-dirs (verified `kb_lib.py:133-136` skips `_`/`.`-prefixed); test asserts no `.kb-save-` tmp remains, adjust test | P2 | Stops unbounded disk growth in content folders; backups stay restorable but invisible |
| 5 | Add a one-line audit log per save/new (R4) | `kb-admin/server.js` (save handler) | +3 | Low | P2 | Journald-backed "who saved what when" without new deps |
| 6 | Delete the drifted `kb-admin/buttonsbebe-kb-admin.service` copy; keep only `deploy/systemd/` | `kb-admin/...service` (15 LOC file) | −15 | Low — verify install scripts point at `deploy/systemd/` (CD fingerprint-gates `deploy/systemd`, `buttonsbebe-deploy-receive.sh:112`; no reference to the kb-admin copy found in `deploy/cd/`) | P2 | One source of truth for the unit; today's drift proves the two-copy pattern fails |
| 6b | *(alternative to 6, if any VPS runbook references the in-module copy — check before deleting)* add a comment in the kb-admin copy pointing at `deploy/systemd/` as authoritative | `kb-admin/...service` | +1 | Zero | P2 | Same goal, zero deletion risk |
| 7 | Cache `/health` product scan for 60 s (R7) | `kb-admin/server.js:103` | +5 | Low | P3 | Bounded work per request; still fresh enough for the freshness signal it feeds |
| 8 | Record the cross-process lock convention (R6) in `kb/CONVENTIONS.md` — markdown saves lock-free atomic, indexers flock, notices lock-dir | `kb/CONVENTIONS.md` | +4 | Zero (doc) | P3 | Prevents a future maintainer from "harmonizing" locks in either direction without knowing the trade-off |

Priority rationale: #1/#2 are correctness of the running service (and both
land in one tiny commit); #3-#6 are maintainability with proof; #7/#8 are
polish. **No dependency additions proposed.** The honest headline: this
module is 329 lines, its security-critical path is well tested (see
Verification), and the two P1s are the only real defects found — "leave it
alone" is 90% of the right answer, with these exceptions.

## Rejected on principle

- **Adopt Express (or any dep)** — the zero-dep posture is itself the
  reliability feature for a file this size; the ≥100-line bar is deliberately
  not met (~60-80 net LOC would be *added* complexity surface via
  transitive deps).
- **Reintroduce the old `kb-editor/`** — retired per RETIRED.md:16; kb-admin
  supersedes it.
- **Shared Node util module with whatsapp-connect** — two services sharing a
  `common/` module for `send()`/body parsing would couple deploy/restart of
  unrelated services for ~20 LOC.
- **Auth token inside kb-admin** — Caddy forward_auth is the documented seam
  (AGENTS.md §2.3); a second app-level check would be redundant and another
  secret to rotate.
- **Cross-process lock for markdown saves** — would tie console save latency
  to index builds; staleness-until-next-reindex is the accepted workflow
  (console copy text, `console-src/index.html:773`).
- **Rate limiting / per-user identity in kb-admin** — one trusted human
  editor; journald audit (action 5) covers accountability.
- **Merging the console-HTML assertions out of
  `kb-admin/test/server.test.js:147-189`** — moving them to
  `console-src/test/` is cosmetic churn; the gate already runs both.

## Verification

Per P1/P2, existing coverage or the ONE smallest new test:

| Action | Existing covering test | Else the one smallest new test |
|---|---|---|
| #1 URL hardening | None — current suite never sends a malformed request line. | Add to `kb-admin/test/server.test.js`: `fetch` can't send raw `//`, so one raw-socket test: connect, write `GET // HTTP/1.1\r\nHost: x\r\n\r\n`, assert response is HTTP 400 and — critically — that a *subsequent* `GET /health` on a new connection still returns 200 (proves no process death). ~12 LOC, same raw-net pattern the crash repro used. |
| #2 stdio drain | None. | Existing suite spawns the server with real `KB_DIR` but `/reindex` spawns `KB/update.sh` which doesn't exist in the temp KB → child errors, `error` event sets `ok:false` — already the case; pipe-fill needs a real chatty script. Smallest test: in `startServer`'s temp KB write an `update.sh` that prints 10 MB to stdout then exits 0; POST `/reindex`; poll `/reindex-status` until `running:false, ok:true` with a timeout (say 10 s). If stdio isn't drained, the child hangs and the poll times out → test fails. ~15 LOC. |
| #3 dead `loadNotices` | Deletion itself is the verification: `node --test kb-admin/test/*.test.js` + `node --check` must stay green (they are in the gate, `tools/verify_release.sh:231-232`). | — |
| #4 `.backups/` relocation | Existing interrupted-save test (`server.test.js:211-230`) asserts no `.kb-save-*` tmp remains and mode preserved — extend with: after a successful save, assert no `*.bak-*` inside `KB/<folder>/` and the backup exists under `KB/.backups/`. ~5 LOC. | — |
| #5 audit log | New: capture child stderr in `startServer` (already captured, `server.test.js:50-51` and asserted empty, line 57!) — note: **the suite already asserts `stderr === ""`**, so the audit line must go to **stdout**, not stderr, or that assertion must be relaxed deliberately. One save via `/save`, then assert the captured stdout contains the path string. ~6 LOC. | — |
| #6 unit-copy deletion | `grep -rn "kb-admin/buttonsbebe-kb-admin.service"` across repo + `deploy/` runbooks before deleting; gate's `bash -n` and manifest list don't reference it. | If referenced anywhere, fall back to 6b (comment pointer). |
| #7 health cache | Existing health test (`server.test.js:62-72`) still passes if cache is time-based and test writes files before first request. If flaky, make TTL an env var and set it to 0 in tests (pattern already used: `KB_PRODUCT_FRESH_HOURS`, `server.test.js:47`). | — |
| #8 doc line | Doc change; no test. The *convention's* behavior is already pinned cross-language by the notice-lock tests: `server.test.js:85-99` (Node honors the Python-created lock dir) and kb-side tests for `notices_lib`. | — |

Existing security coverage worth naming (all in `kb-admin/test/server.test.js`):
traversal rejection (`:74-83`), symlink file + symlink-folder bypass
(`:200-209` — this is the strongest test in the suite), oversized/malformed
JSON leaving content untouched (`:191-199`), interrupted atomic publication
preserving old content and file mode (`:211-230`), notice lock interop with
Python (`:85-99`), spoofed `created_by` rejected (`:123-137`). The
security-critical paths ARE tested; the two P1s are the gaps.

## Path traversal defense — audit detail (question (a))

`safePath` (`server.js:25-38`) is the module's #1 audit point. Verdict:
**sound — a charset allowlist on top of structural checks, stronger than the
usual resolve+prefix pattern, with a real symlink story.** Line by line:

1. `p.includes("..")` rejects any `..` substring (`:26`) — blunt but here
   *sufficient because* of what follows; it is not the only defense.
2. Exactly two `/`-split parts (`:27-28`) — depth is fixed at `folder/name`,
   so no subdirectory traversal is representable.
3. Folder must be in `FOLDERS` allowlist `intents|faq|policies|tickets`
   (`:30`) — `products/`, `learned/`, `shopify/`, `notices/` structurally
   unreachable. **This is the security invariant; it cannot regress without
   touching this line.**
4. Name charset `^[A-Za-z0-9._-]+\.md$` (`:31`) — no `/`, no `\`, no NUL, no
   `%`; percent-decoding already happened in `new URL`'s
   `searchParams.get` (WHATWG percent-decodes once, so `%2e%2e%2f` becomes
   literal `../` and dies at check 1; double-encoding survives as literal
   `%25` which fails the charset). Backslash: `\` is not in the charset —
   on Linux `path.join` would treat it as a filename char anyway, but it's
   rejected pre-join. NUL bytes fail the charset.
5. **Symlinks:** the parent folder must not itself be a symlink and must
   resolve inside realpath(KB) (`:34`); an existing candidate must be a
   regular file and not a symlink (`:35`). Race window between `lstat` and
   use exists in principle (TOCTOU), and `atomicSave` compensates with
   O_NOFOLLOW on the tmp open (`:47`) and re-running `safePath` before
   rename (`:50`) plus link-based publication for new files (`:58`) so a
   swapped-in symlink is never followed into during publication. Verified by
   the symlink tests (`server.test.js:200-209`).
6. `atomicSave` independently re-validates: `safePath(rel)!==fp` throws
   (`:41`).

Weaknesses found (all labeled, none exploitable given localhost+Caddy, all
with the same root): the pre-checks use `lstat`/`realpath` at request time —
a local attacker who can write to KB already wins regardless (it's the same
trust domain); no path in the reachable threat model defeats it. The
`p.includes("..")` line alone would be a string-matching hack; **the charset
allowlist at `:31` is what actually makes it provable** — candidate names are
drawn from a finite charset that cannot encode traversal. I would sign off
on this function. One nit: `safePath` returning the joined `candidate` while
`atomicSave` compares `safePath(rel)!==fp` (`:41`) — string equality of
paths only holds because both derive from the same `path.join`; it's correct
but subtle; a comment would help the next auditor.

## Auth trust (question (e))

The service performs **zero** authentication: no token, no cookie check, no
`X-Forwarded-For` parsing — nothing in `server.js` mentions auth. Trust is
100% in (1) `listen(PORT, "127.0.0.1")` (`server.js:329`) and (2) Caddy's
`forward_auth` gate on `/console/kbapi/*`
(`deploy/caddy/sites/support.caddy:108-115`, which proxies only after
`/auth/check` succeeds). This is safe and correctly documented: the header
comment states the model (`server.js:1-7`), AGENTS.md §2.3 names Caddy
forward_auth as the gate, and `deploy/tests/test_caddy_config.py:89,133`
pins the routing so the seam can't silently disappear. Consistency verified
with `whatsapp-connect` (same pattern). Nothing to change. The one latent
concern is R1: the process-death vector is *inside* the auth boundary's
backend, i.e. anything that can reach localhost can DoS it — which is the
standard trade for localhost-bound services and acceptable here.

## Hand-rolled HTTP detail (question (b))

- **Routing:** if/else chain on `req.method` + exact `u.pathname` matches
  for 9 routes (`server.js:209-326`). Correct, trivially auditable, zero
  magic. ~120 LOC total including handlers.
- **URL parsing:** WHATWG `new URL(req.url, "http://x")` (`:206`) — modern
  API, percent-decoding handled — **except the unhandled-throw crash (R1)**.
- **Body parsing (`readBody`, `:75-90`):** 1 MiB cap with 413
  (`:79`), 10 s timeout with 408 (`:77`), request-error 400 (`:80`),
  JSON.parse try/catch 400 (`:85-86`), JSON must be a non-array object
  (`:87`). Solid — covers the usual hand-rolled pitfalls (slowloris body,
  oversized body, wrong content). Content-Type is *not* enforced (any POST
  with JSON body works) — acceptable for a single-user console API behind
  auth; enforcing it would be 2 LOC of churn with no threat it closes.
- **Error responses:** uniform `{error}` JSON via `send()`; `String(e)`
  leaks exception text to the client on save failures (`:254`) — mild info
  disclosure to an already-authenticated owner; not worth changing (the
  reader is the trusted editor).

## Concurrent write handling (question (c))

`atomicSave` (`:39-65`) is tmp + fsync + rename for existing files, tmp +
link for new files (exclusive publication), mode preservation from the
existing file, directory fsync after publish, and cleanup of tmp on any
failure path. Two editors hitting the same file: last-writer-wins per
*complete* file — no torn writes, readers (and the indexer) never see a
partial file. `.bak-*` per save gives per-edit recovery. Nightly promotion
overlap: analyzed in R6 — no clobber, possible staleness until next
reindex; convention recorded as action #8. **Named overlap for the kb/
analyst:** notice lock conventions are duplicated across
`kb-admin/server.js:167-198` and `kb/scripts/notices_lib.py:87-137` — one
shared file contract, two implementations, matched semantics today; the
Node side should add the temp-file fsync to match Python (`server.js:193`
vs `notices_lib.py:131`) if touching that code anyway (not worth its own
action).

## Reindex trigger (question (d))

`POST /reindex` → `spawn("/bin/bash", [path.join(KB, "update.sh")], { cwd:
KB })` (`server.js:261`). **No injection surface:** the filename is a
constant path built from `KB_DIR` (env, root-controlled), not from any
request field; request data never reaches argv. Coupling direction: kb-admin
→ KB scripts, one-way, coarse (rebuild everything) — appropriate; the
script has its own `set -e` and flock'd indexer. Concurrency guard is a
single in-process flag (`:259`), which does not fence against the nightly
timer also running `index_kb.py` — harmless because the indexer's own flock
(`index_kb.py:40-52`) makes the loser fail fast, and `update.sh` inherits
that failure as a nonzero exit recorded in `reindex.ok`. Latent pipe
deadlock: R3 above.

## Test coverage (question (f))

`kb-admin/test/server.test.js` — 8 tests: health counts/freshness
(`:62-72`), traversal + missing-file discrimination (`:74-83`), notice-lock
interop with Python-written lock dir (`:85-99`), notice text/deadline
validation leaving store untouched (`:101-121`), server-derived
`created_by` anti-spoof (`:123-137`), corrupt store → 503 not empty board
(`:139-145`), console HTML contract assertions (`:147-189` — misplaced but
harmless), malformed/oversized JSON leaving content untouched (`:191-199`),
symlink file+folder bypass blocked (`:200-209`), interrupted atomic
publication preserving content+mode (`:211-230`). **The security-critical
paths — traversal, allowlist, symlink, atomicity — are all covered** (the
symlink test is excellent: it proves both read and write bypass fail and
that the private file is untouched). The gate runs the suite
(`tools/verify_release.sh:231-232`). Gaps: malformed request line (R1's
crash), reindex child lifecycle (R3), save audit visibility (R4's follow-on).
The suite's `stderr === ""` assertion (`server.test.js:57`) is a good
crash-canary: any uncaught non-URL exception in a handler would fail the
suite — but it does NOT catch R1 because no test sends a bad request line.

## LOC total (question (g))

`server.js` = 329 lines. The honest headline finding: **this module is
small, its security-critical paths are well built and well tested, and the
right prescription is mostly "leave it alone."** The exceptions are two
tiny P1 fixes (uncaught-throw crash on a malformed request line; reindex
pipe deadlock), both single-digit LOC, both verified by direct experiment /
code reading respectively. Everything else is P2/P3 polish. Total proposed
delta across all actions: roughly +25/−20 LOC.
