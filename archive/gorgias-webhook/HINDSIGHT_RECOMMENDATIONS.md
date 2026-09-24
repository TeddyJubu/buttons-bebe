# Hindsight Integration — Recommendations for Missing Parts
**2026-06-26**

## What's now set up

1. **Hindsight Docker container** — running on localhost:8888 (API) + :9999 (UI)
   - LLM: Ollama Cloud / deepseek-v4-flash
   - Persistent volume: `hindsight-data`
   - Auto-restarts on reboot (`--restart unless-stopped`)

2. **hindsight_integration.py** — bridge module with 5 functions:
   - `retain_ticket_experience()` — called by Workflow B on every agent reply
   - `retain_owner_answer()` — called when Chaim answers a KB gap
   - `retain_kb_content()` — seeds Hindsight with static KB content
   - `recall_relevant_memories()` — called by draft_engine alongside pgvector
   - `reflect_on_patterns()` — for weekly review / pattern discovery

3. **Wired into server.py (Workflow B)** — every human agent reply is now
   retained into Hindsight as an experience memory (PII-scrubbed)

4. **Wired into draft_engine.py** — Hindsight memories are recalled alongside
   pgvector KB chunks and fed into the LLM draft prompt. If the KB has a gap
   but Hindsight has a learned memory, the draft can still be generated.

5. **seed_hindsight.py** — one-time script to populate Hindsight with existing
   KB content (policies, intents, FAQ, exemplar tickets)

## What's still missing (and how to handle each)

### 1. Owner Q&A trigger (KB gap → Chaim answers → Hindsight + KB)

**The gap:** When the KB can't answer a question (kb_gap=True), the system
should ask Chaim for the answer. His answer needs to go into BOTH the KB
(kb/learned/owner-qa-*.md via kb_writeback) AND Hindsight (via
retain_owner_answer). Right now neither trigger is wired.

**Recommendation:** Add a Telegram bot command `/kb_gap` that:
- Lists tickets with kb_gap=True from feedback.db
- Chaim replies with the answer
- The answer is written to kb/learned/ via kb_writeback.record_owner_answer()
- AND retained into Hindsight via retain_owner_answer()
- The ingestion worker picks up the new KB file on next sync

This is a ~2 hour task. The machinery (kb_writeback + hindsight_integration)
is already built — just needs the Telegram command handler.

### 2. Agent reply promotion (good reply → KB learned file)

**The gap:** Workflow B captures agent replies and compares them to AI drafts,
but there's no way to promote a good reply to the KB. kb_writeback has
`record_approved_reply()` but nothing triggers it.

**Recommendation:** Add a Telegram command `/promote <ticket_id>` that:
- Fetches the ticket's reply from feedback.db
- Runs PII scrubbing
- Calls kb_writeback.record_approved_reply() → writes kb/learned/ticket-<id>.md
- The Hindsight retain already happened automatically via Workflow B
- The ingestion worker picks up the new file on next sync

Chaim should only promote replies that contain generalizable knowledge
(not one-off order-specific answers). The review_pending banner in the
file reminds him to vet it.

### 3. Overnight enrich write-back to kb/learned/

**The gap:** The overnight batch pipeline enriches clusters into canonical
Q&A pairs (enriched_clusters.jsonl) but doesn't write them back to
kb/learned/ as markdown files. They're stuck in JSONL.

**Recommendation:** Add a stage 5 to kb_overnight_worker.py:
- Read enriched_clusters.jsonl
- For each cluster with confidence >= 0.8 and sensitive=false:
  - Generate a CONVENTIONS-correct markdown file in kb/learned/
  - Status: review_pending (Chaim must confirm before it's trusted)
  - Git commit + ingestion_worker.sync()
  - Also retain into Hindsight via retain_kb_content()
- For sensitive=true clusters (refunds, disputes): skip, flag for manual review

This is a ~1 hour task. The enriched data is already being generated.

### 4. Weekly review with Hindsight reflect

**The gap:** Workflow C (weekly_review.py) reads feedback.db for metrics
but doesn't use Hindsight's reflect capability to find patterns.

**Recommendation:** Add to weekly_review.py:
- `reflect_on_patterns("What questions did the AI draft get wrong most often?")`
- `reflect_on_patterns("What issues do customers most commonly escalate?")`
- `reflect_on_patterns("What policies are customers most confused about?")`
- Include the reflections in the weekly Telegram report

This requires accumulated data (a few weeks of agent replies) to be useful.
Wire it now, let it accumulate, and it'll start producing insights in 2-3 weeks.

### 5. Hindsight systemd service

**The gap:** Hindsight runs as a Docker container with `--restart unless-stopped`,
which handles reboots. But it's not managed by systemd like the other services.

**Recommendation:** Install the systemd unit at infra/hindsight/hindsight.service:
```
cp infra/hindsight/hindsight.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable hindsight
```
This gives unified logging via journalctl and consistent lifecycle management.

### 6. Hindsight UI access (optional)

**The gap:** Hindsight has a web UI at localhost:9999 but it's only accessible
from the VPS itself.

**Recommendation:** Add a Caddy reverse proxy entry for `memory.<domain>` that
points to :9999 with basic_auth. This lets Chaim browse memories, see what
the system has learned, and manually review/promote content. Optional — the
API works fine without it.

## Architecture with Hindsight

```
                         ┌──────────────────────────────────────────────────────┐
                         │                    VPS (srv1766050)                   │
                         │                                                      │
  Gorgias ──webhook──▶ Caddy ──▶ server.py ──▶ pipeline.py                      │
                                          │           │                          │
                                          │     classifier  draft_engine         │
                                          │           │        │                 │
                                          │           │     ┌──┴──┐              │
                                          │           │     │     │              │
                                          │     ┌─────┘     │     │              │
                                          │     ▼           ▼     ▼              │
                                          │  pgvector    Hindsight  model_gateway │
                                          │  (KB facts)  (learned)    │           │
                                          │     ▲           ▲        ▼           │
                                          │     │           │    Ollama Cloud     │
                                          │  ingestion     Workflow B            │
                                          │  worker        (retain replies)      │
                                          │     ▲                                │
                                          │     │                                │
                                          │  Git repo (kb/*.md)                  │
                                          │     ▲                                │
                                          │  Chaim edits                         │
                                          │                                      │
                                         Docker: hindsight + kb-postgres        │
                                         └──────────────────────────────────────────────┘
```

## Key design principles preserved

1. **PII never leaves the VPS** — all text is scrubbed before going to Hindsight
2. **Hindsight is optional** — if it's down, the system continues working
3. **KB is still the source of truth** — Hindsight is a complement, not a replacement
4. **Human-in-the-loop** — learned memories are review_pending until confirmed
5. **No auto-writing to KB** — promotion is deliberate, not automatic