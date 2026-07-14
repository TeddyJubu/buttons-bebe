# Phase 1 Knowledge Base Architecture — Buttons Bebe

**Version 1.0 • 2026-06-26 • single-editor Phase 1**

This document is the canonical spec for the **knowledge-base / retrieval layer**
of the Hermes AI support agent. It supersedes the KB sections of
`SYSTEM_WORKFLOW.md` (which now defer here). The agent's overall flow
(Workflows A/B/C) is unchanged — only *how knowledge is stored and retrieved*
changes.

---

## Decision

The original PRD proposed **Obsidian + Self-hosted LiveSync + CouchDB +
Supermemory + Ollama Cloud + Caddy** — six tiers. That sync tier
(Obsidian/LiveSync/CouchDB) exists to reconcile edits across many
devices/editors.

**For Phase 1, Chaim is the only knowledge editor.** With a single editor,
the sync tier pays all of its operational cost (a database to run, sync
conflicts to debug, E2E keys to manage, a third backup target) for none of its
benefit. So it is **cut**. Git replaces it as the source of truth.

This keeps the part that genuinely serves the agent — Supermemory's
hybrid-search retrieval API — and removes the two biggest risk areas at once:
the sync tier and the under-specified "vault-mirror" ingestion logic (change
detection becomes `git diff`).

---

## Architecture

```
   Chaim edits Markdown                Hermes agent (server.py / draft_engine.py)
        |                                        |
        | git commit/push                        | hybrid-search query (HTTPS)
        v                                        v
   Git repo (source of truth) ───▶ Ingestion worker ───▶ Supermemory ◀── Caddy (memory.<domain>)
   policies / FAQ / tickets /        (git diff →            (local embeddings        basic_auth +
   learned/ (auto-committed)          push changes)          + graph engine)          bearer token
                                                                  ▲
                                          Ollama Cloud ───────────┘
                                          (LLM inference: classify, draft, summarize)
```

### Components

| Component | Role | Notes |
|-----------|------|-------|
| **Git repo** | Source of truth for all KB Markdown | Private repo. Free versioning, history, diff. Replaces the Obsidian vault + CouchDB. |
| **Ingestion worker** | Detect changed Markdown, push to Supermemory | Change detection = `git diff` between the last-ingested commit and `HEAD`. Far simpler/safer than mirroring a synced vault. |
| **Supermemory** (self-hosted) | Hybrid-search retrieval API (semantic + keyword + graph) | Local embeddings + embedded graph engine. The machine-readable retrieval layer Hermes queries. |
| **Ollama Cloud** | LLM inference for classify / draft / summarize | Inference offloaded off the VPS. The chosen model gateway for Phase 1 (see open items re: OpenRouter consolidation). |
| **Caddy** | TLS termination + reverse proxy for the **one** public KB endpoint | `basic_auth` + Supermemory bearer token = defense in depth. Auto Let's Encrypt. Already fronts the webhook server. |

### Endpoints

- `memory.<your-domain>` (Caddy → Supermemory) — the **only** public KB
  endpoint. The original design's `notes.<your-domain>` (CouchDB/LiveSync) is
  **removed**, cutting a second cert/auth/monitoring surface.
- Supermemory and the Git repo are otherwise **not** exposed to the public
  internet.

---

## Source of truth: the Git repo

KB content lives as `.md` files in a private repo, roughly:

```
kb/
  policies/        shipping-policy.md, return-policy.md, sizing-guide.md
  faq/             faq.md, ...
  tickets/         resolved-ticket-<id>.md   (curated exemplars)
  learned/         auto-committed agent replies + owner Q&A (reviewable)
```

- **policies / faq / tickets** — written and curated by Chaim (manual, the
  static-ish corpus that is most of Phase 1).
- **learned/** — where the feedback loop deposits machine-grown knowledge as
  Markdown (see "Integration" below). Keeping it in a separate folder lets
  Chaim review/prune what the system learned, and keeps **Git as the single
  write path into Supermemory** (no second ingestion route to maintain).

> **Alternative authoring surface:** Notion. Since the project already lives in
> Notion, Chaim could maintain the KB there and the worker ingests from Notion
> instead of Git. Slightly more ingestion glue; zero new editing tools for
> Chaim. Git is the recommendation for Phase 1 (versioning + trivial change
> detection); Notion is a drop-in swap of the source tier only.

---

## Ingestion worker (the piece to spec carefully)

This is the weakest link in any "push Markdown into a retrieval store" design,
so it must be specified — it is where most of the bugs will live.

- **Trigger:** repo webhook on push (preferred), or a periodic pull-on-change.
- **Change detection:** `git diff --name-status <last_ingested_sha>..HEAD`.
  Persist `last_ingested_sha` so restarts are idempotent.
- **Per file:** `A`/`M` → upsert into Supermemory (stable doc id = repo-relative
  path); `D` → delete from Supermemory; `R` → delete old id + upsert new.
- **Chunking:** split long Markdown on headings; keep front-matter (tags,
  source_type) as metadata for filtered retrieval.
- **Failure/retry:** on partial failure, do **not** advance `last_ingested_sha`;
  retry with backoff; log to the same logging surface as `server.py`.
- **Idempotency:** re-ingesting the same commit must be a no-op.

---

## Integration with the agent workflows

The KB *mechanism* changes; the workflow *shape* does not.

- **Retrieval (Workflow A, step A8):** instead of difflib over a SQLite
  `kb_entries` table, the agent issues a **hybrid-search query to Supermemory**
  (`memory.<domain>`) with the normalized customer message. Top-N results (with
  their source metadata) become the draft prompt's KB context. No match above
  Supermemory's relevance threshold → `kb_gap=1` → owner Q&A.
- **Drafting (step A9):** the draft LLM call runs on **Ollama Cloud**.
- **Owner Q&A (step A8b):** the owner's answer is written as a Markdown file
  under `kb/learned/owner-qa-<timestamp>.md` and committed → the worker ingests
  it → next similar question retrieves it from Supermemory.
- **Agent-reply learning (Workflow B, step B5):** when an agent reply validates
  a draft (similarity ≥ 0.7), the reply is written as Markdown under
  `kb/learned/ticket-<id>.md` and committed → ingested. (The similarity itself
  is still computed in `feedback.db` — see below.)

### What stays in SQLite (`feedback.db`)

The `kb_entries` table is **removed** — retrieval now lives in Supermemory.
The **operational** tables stay, because they track agent-vs-AI performance,
not knowledge retrieval:

- `drafts` — every draft the AI produced.
- `replies` — every agent reply captured from webhooks (dedup by `message_id`).
- `comparisons` — draft↔reply similarity (`difflib`), edit ops, response time.

Workflow C (weekly review) still reads `feedback.db` for metrics.

---

## Persistence & backups

- **`SUPERMEMORY_DATA_DIR`** — persisted volume; backed up (embeddings + graph).
- **Git repo** — is itself the KB backup (full history); mirror/clone offsite.
- **`feedback.db`** — small SQLite file; include in the existing backup job.
- The original design's `/srv/couchdb/data` volume and its backup job are
  **removed**.

---

## Security

- TLS terminated at Caddy (auto Let's Encrypt), same as the webhook server.
- `memory.<domain>` protected by `basic_auth` **and** a Supermemory bearer
  token (defense in depth).
- Supermemory + Git repo not publicly reachable.
- One public KB subdomain instead of two → smaller credential/cert surface.

---

## What was dropped (and why it's reversible)

**Dropped:** Obsidian, Self-hosted LiveSync, CouchDB, the `notes.<domain>`
endpoint, the `/srv/couchdb/data` volume + its backup, and the SQLite
`kb_entries` retrieval table.

**Why safe for Phase 1:** draft-only, human-in-the-loop, ~$300 budget, a small
mostly-static corpus, one editor. Net effect: failure surface drops by ~⅓,
comfortably fits the entry-tier Hostinger VPS (Supermemory's embeddings + graph
no longer share the box with CouchDB).

**Reversible in Phase 2:** if multiple non-technical editors appear and want a
notes-app editing experience, reintroduce Obsidian + LiveSync **as a new source
tier feeding the same ingestion worker** — the Supermemory retrieval layer and
everything downstream of it do not change.

---

## Open items / risks to validate

1. **VPS sizing.** Supermemory still runs local embeddings + an embedded graph
   engine on the same box as Caddy + `server.py`. Validate memory headroom
   against the actual Hostinger plan before committing.
2. **Model-gateway consolidation.** Earlier project work leaned on **OpenRouter**;
   this introduces **Ollama Cloud**. Phase 1 keeps Ollama Cloud, but decide
   whether to standardize on one gateway to avoid two key-management/billing
   surfaces.
3. **Supermemory maturity.** Self-hosted Supermemory is a young project — treat
   its API stability and upgrade path as a real risk for a client deliverable;
   pin a version and watch the changelog.
4. **Ingestion worker** is custom glue — the most likely source of bugs. Build
   and test it against the change-detection spec above before wiring it into
   the live flow.
