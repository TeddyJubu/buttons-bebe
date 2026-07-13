# 10 · GitHub Setup & Onboarding

> **SUPERSEDED (2026-07-14):** This historical handover chapter is not current operational documentation. Do not use its counts, runtime status, write-path descriptions, or instructions. Use the repository-root `CLAUDE.md`, the user-provided `AGENTS.md`, and live verification instead.

*What this doc covers: the repo's current git state, how to publish it to GitHub, the branch strategy, and a first‑week onboarding checklist for the new team.*

*Sources: live `git` inspection of this repo, `.gitignore`, and docs 06 + 09.*

---

## 1. Current git state

- The project **is** a git repository, currently on branch **`main`**.
- Branches present: **`main`** (live Hermes system, ~119 tracked files) and **`Fable_buttonsbebe`** (adds the Fable rebuild, ~227 tracked files).
- History is short (initial import + a few feature commits) — this repo was created recently to hand the project over.
- **No GitHub remote is configured yet.** Publishing it is step 2 below.
- Secrets and customer data are correctly kept out of git by `.gitignore` (see §4).

Verify any time with:

```bash
git branch -a           # list branches
git log --oneline -10   # recent history
git remote -v           # remotes (currently empty)
git status              # working tree state
```

---

## 2. Publish to GitHub (first push)

> You (or Tony) run these once, from the repo root, signed in to the GitHub account that should own the repo. Setting a remote does **not** need special permissions; only the `push` does, and it uses your normal GitHub login.

**a. Create an empty private repo on GitHub** (no README/License/gitignore — the repo already has them). Copy its URL, e.g. `https://github.com/<org>/<repo>.git`.

**b. Add the remote and push both branches:**

```bash
cd "/path/to/Shopify help desk"

# point 'origin' at the new GitHub repo
git remote add origin https://github.com/<org>/<repo>.git

# push the live system branch and set it as the default upstream
git push -u origin main

# also push the Fable rebuild branch
git push origin Fable_buttonsbebe
```

**c. On GitHub**, set `main` as the default branch, and (recommended) mark the repo **private** — this codebase references production infrastructure.

> **Before the very first push, do the secret scrub in §4.** A populated `.env` and an `.env.bak-*` exist on disk locally; they are git‑ignored so they won't be pushed, but confirm with `git status --ignored` and never `git add -f` them.

If Tony has already given the repo URL, the remote may already be wired up — check `git remote -v`.

---

## 3. Branch strategy (recommended)

| Branch | Purpose | Rule |
|---|---|---|
| `main` | The live production system (Hermes). | Protected. Only merge reviewed, tested changes. This is what's deployed. |
| `Fable_buttonsbebe` | The offline rebuild (Fable). Keep it alive until you decide its fate (doc 07). | Treat as a long‑lived feature branch or archive it once a decision is made. |
| `feature/*` | Your day‑to‑day work. | Branch off `main`, open a PR, review, merge. |

Turn on **branch protection** for `main` on GitHub (require PR review, block force‑push). Because the live system's `webhook/` and `processor/` source isn't in the repo yet, your **first PR should be "complete the repo"** — committing the VPS‑pulled source (doc 06) after scrubbing secrets.

---

## 4. Never commit secrets or customer data (safety net)

The repo's `.gitignore` already blocks the sensitive things. Keep it that way:

- `.env`, `**/.env`, `.env.bak-*` — credentials. **Never commit.**
- `data/` — customer ticket exports containing **PII**. **Never commit.**
- `_VPS-FULL-BACKUP-*/` — the old server backup, contains **plaintext secrets**. **Never commit.**
- `__pycache__/`, `*.pyc`, `node_modules/`, `.venv/`, `.DS_Store` — noise.

**Pre‑push secret scrub checklist:**

```bash
git status --ignored          # confirm .env, data/, backups show as Ignored
git ls-files | grep -i -E 'env|secret|token|password'   # should return nothing sensitive
git grep -i -E 'api[_-]?key|secret|password|token' -- ':!HANDOVER' ':!*.example'  # eyeball hits
```

If you pull the `webhook/`/`processor/` source off the VPS (doc 06), **strip its `.env` and any tokens before committing.** When in doubt, rotate the key.

---

## 5. First‑week onboarding checklist

**Day 1 — understand**
- [ ] Read `HANDOVER/README.md`, then `01`, `02`, `06`.
- [ ] Read the repo‑root `CLAUDE.md` (current source of truth for the live system).
- [ ] Skim `03`, `04`, `05` to see how the pieces map to code.

**Day 1–2 — get access** (request these from Tony / Chaim — see §6)
- [ ] SSH access to the VPS `srv1766050` (`2.25.137.77`).
- [ ] Gorgias, Shopify, and Redo credentials/logins.
- [ ] Ollama Cloud account/key used by the Hermes brain.
- [ ] The GitHub repo (owner/admin access).

**Day 2–3 — complete the repo**
- [ ] Follow doc `06`'s read‑only pull procedure to copy `webhook/`, `processor/`, and the Hermes home (`~/.hermes/`) off the VPS.
- [ ] Scrub secrets/PII (§4), then commit as your first PR into `main`.

**Day 3–4 — verify the live system (read‑only)**
- [ ] Run the verify commands in doc `05` against the server: `hermes mcp list`, `systemctl status` on the services, `journalctl` on the processor, a KB `search.sh` test, and the SQLite queue query. Everything should be green **without you changing anything**.

**Week 1 — decide & plan**
- [ ] Read `07` and decide: continue Fable, fold it into `main`, or shelve it.
- [ ] Read `08`, confirm Phase 2 scope, and get Chaim's answers to the **5 policy questions**.
- [ ] Work through the day‑one remediation checklist in `06` (rotate weak WhatsApp creds, rotate `.env` keys, consolidate the two `.env` files).

---

## 6. Access & credentials to collect at handover

Ask Tony / Chaim for the following (this is the "keys to the car" list). **Do not** paste any of these into the repo or this handover.

| Item | Why you need it | Where it's used |
|---|---|---|
| SSH key/login for VPS `srv1766050` | Run/verify the live system; pull the missing source | The production server |
| Gorgias account + API key (email + key, "Basic" auth) | Read tickets, write internal notes | The 3 MCP tools + webhook |
| Shopify store + API client id/secret ("client‑credentials") | Read order/product data; product sync | MCP tools + KB sync |
| Redo API key + store id ("Bearer" auth) | Returns/refunds status | Redo MCP tool |
| Ollama Cloud key | Runs the `glm-5.2` model that is the AI brain | Hermes config (`~/.hermes/config.yaml`) |
| GitHub repo owner access | Push, protect branches, manage the team | GitHub |
| Domain/Caddy access (`srv1766050.hstgr.cloud`) | The public HTTPS entry point | Caddy reverse proxy |
| WhatsApp number/session for escalations | Owner escalation alerts | `whatsapp-connect` service |

Doc `05` maps each credential to the exact service and environment variable that consumes it.
