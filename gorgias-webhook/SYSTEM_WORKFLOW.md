# Gorgias AI Support Agent — Final System Workflow
# Buttons Bebe | Version 1.0 | 2026-06-24

==============================================================================
OVERVIEW
==============================================================================

A Hermes-based AI agent that monitors Gorgias tickets, drafts replies as
internal notes for human approval, learns from agent edits, and notifies the
business owner for urgent tickets or when it lacks knowledge to answer.

The system has two trigger paths (customer message, agent message), a
knowledge base that grows from real agent replies, and a human-
in-the-loop learning mechanism via Telegram.

==============================================================================
ARCHITECTURE — COMPONENTS
==============================================================================

1. Webhook Receiver       — server.py (existing, port 8080 behind Caddy)
2. Gorgias API Client      — gorgias_api.py (existing + new methods to add)
3. Feedback Database       — feedback.db (SQLite: drafts/replies/comparisons)
4. Knowledge Base          — Git repo (Markdown, source of truth) +
                             ingestion worker + Supermemory retrieval API
                             behind Caddy at memory.<domain>.
                             See PHASE1_KB_ARCHITECTURE.md for the full spec.
5. Telegram Notifier      — telegram_notify.py (new)
6. Drafting Engine         — draft_engine.py (new, LLM via Ollama Cloud)
7. Priority Classifier     — classifier.py (new)
8. Weekly Review Cron      — weekly_review.py (new, cron job)

==============================================================================
DATABASE SCHEMA — feedback.db
==============================================================================

-- Every draft the AI produces
CREATE TABLE drafts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id           INTEGER NOT NULL,
    customer_message    TEXT NOT NULL,        -- what the customer asked
    draft_text          TEXT NOT NULL,        -- the AI's drafted reply
    priority            TEXT NOT NULL,        -- immediate / high / low
    classification_reason TEXT,               -- why it was classified
    kb_sources          TEXT,                 -- JSON: which KB entries were used
    kb_gap              INTEGER DEFAULT 0,    -- 1 if no KB match found
    kb_gap_question     TEXT,                -- the question sent to owner
    kb_gap_answer       TEXT,                -- owner's answer (saved to KB)
    customer_email      TEXT,
    order_context       TEXT,                 -- JSON snapshot
    conversation_snippet TEXT,                -- last few messages for context
    model_used          TEXT,
    confidence          REAL,
    status              TEXT DEFAULT 'drafted', -- drafted/matched/no_reply/superseded
    matched_reply_id    INTEGER,              -- FK to replies.id
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Every agent reply captured from webhooks
CREATE TABLE replies (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id           INTEGER NOT NULL,
    message_id          INTEGER,              -- Gorgias message ID (dedup)
    reply_text          TEXT NOT NULL,
    sender_email        TEXT,                 -- which agent replied
    channel             TEXT,                 -- email / chat / etc.
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Computed metrics linking drafts to replies
CREATE TABLE comparisons (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id            INTEGER NOT NULL REFERENCES drafts(id),
    reply_id            INTEGER NOT NULL REFERENCES replies(id),
    similarity          REAL,                 -- 0.0 to 1.0
    exact_match         INTEGER DEFAULT 0,    -- 1 if identical
    edit_ops            TEXT,                 -- JSON: {added, removed, replaced}
    response_time_sec   INTEGER,
    computed_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- NOTE: there is no kb_entries table in Phase 1.
-- Knowledge storage + retrieval lives in Supermemory (fed from a Git repo of
-- Markdown), NOT in SQLite. feedback.db holds only operational metrics
-- (drafts / replies / comparisons). See PHASE1_KB_ARCHITECTURE.md.

==============================================================================
KNOWLEDGE BASE
==============================================================================

Full spec: PHASE1_KB_ARCHITECTURE.md. Summary below.

ARCHITECTURE (single-editor Phase 1):
  Git repo (Markdown = source of truth)  -- Chaim is the only editor
    -> ingestion worker (git diff -> push changes)
    -> Supermemory (self-hosted hybrid-search retrieval: embeddings + graph)
    -> Caddy fronts ONE public endpoint: memory.<domain> (basic_auth + bearer)
  LLM inference (classify / draft) runs on Ollama Cloud (offloaded off the VPS).

  The original Obsidian + LiveSync + CouchDB sync tier was DROPPED — it only
  earns its place with multiple editors, and Phase 1 has one. Reversible in
  Phase 2 by adding a vault as a new source tier feeding the same worker.

The KB has three sources that feed Supermemory:

SOURCE 1: POLICY FILES (manual, seeded at setup)
  - kb/policies/*.md, kb/faq/*.md, kb/tickets/*.md in the Git repo
  - Written and curated by the owner upfront. The small, mostly-static corpus
    that is most of Phase 1.

SOURCE 2: AGENT REPLIES (auto-grown from feedback loop)
  When a draft is matched to an agent reply with similarity >= 0.7 (computed in
  feedback.db), the agent's reply is written as Markdown to kb/learned/
  ticket-<id>.md and committed -> the worker ingests it into Supermemory.
  Kept in a separate folder so the owner can review/prune what was learned.

SOURCE 3: OWNER Q&A (human-in-the-loop learning)
  When Supermemory returns no match above its relevance threshold, the system
  Telegrams the owner ("I don't have info to answer this — how should I
  respond?"). The owner's answer is written to kb/learned/owner-qa-
  <timestamp>.md and committed -> ingested -> retrievable next time.

KB RETRIEVAL (at draft time):
  - Issue a hybrid-search query to Supermemory (memory.<domain>) with the
    customer's message. Supermemory handles semantic + keyword + graph ranking.
  - No result above threshold -> kb_gap=1, trigger owner Q&A.
  - Results found -> pass top N (with source metadata) into the draft prompt.

KB MAINTENANCE:
  - The Git repo is the audit trail and edit surface (history + diff for free).
  - Owner edits/prunes kb/learned/ and kb/policies/ directly via Git.
  - Weekly cron reviews draft<->reply metrics in feedback.db (not KB internals).

==============================================================================
WORKFLOW A — CUSTOMER MESSAGE TRIGGER
==============================================================================

TRIGGER: Gorgias fires "ticket.message.created" webhook with from_agent=false

STEP A0: WEBHOOK RECEIPT
  - Gorgias POSTs to https://srv1766050.hstgr.cloud/webhook
  - Caddy terminates TLS, proxies to server.py on localhost:8080
  - server.py validates X-Webhook-Secret against config.json
  - Parses JSON, extracts:
      event_type = "ticket.message.created"
      ticket_id   = data.ticket_id
      from_agent  = data.from_agent  (false = customer)
      body_text   = data.body_text
      sender      = data.sender.email

STEP A1: CHECK FROM_AGENT
  - If from_agent == true -> skip to WORKFLOW B (agent message trigger)
  - If from_agent == false -> continue with WORKFLOW A

STEP A2: FETCH TICKET METADATA
  API: GET /api/tickets/{ticket_id}
  Extract:
    - subject
    - customer id and email
    - status (open/pending/closed)
    - existing tags
    - assignee
    - created_at, updated_at

STEP A3: FETCH FULL CONVERSATION
  API: GET /api/tickets/{ticket_id}/messages
  Extract: all messages, oldest first, including sender, body_text,
  from_agent, timestamps. Keep last 5-10 messages as context window.

STEP A4: FETCH CUSTOMER PROFILE
  API: GET /api/customers/{customer_id}
  Extract:
    - name
    - email, phone
    - notes (existing internal notes about this customer)
    - orders_count
    - created_at (customer since)

STEP A5: FETCH ORDER CONTEXT (Shopify data synced to Gorgias)
  API: GET /api/customers/{customer_id}/orders
  (or use existing gorgias_api.py order-context which wraps this)
  Extract:
    - order number, financial_status, fulfillment_status
    - line items (sku, title, quantity, price)
    - shipping address
    - tracking number (if available)
    - order dates
  Store as JSON snapshot for the draft record.

STEP A6: CLASSIFY PRIORITY
  Run classifier.py with inputs:
    - customer message (body_text)
    - ticket subject
    - order context (financial_status, fulfillment_status)
    - conversation history (repeated follow-ups = higher priority)

  Classification rules:

    IMMEDIATE:
      - Keywords: refund, chargeback, cancel, dispute, "never arrived",
        "wrong item", "damaged", "broken", "missing", "not delivered",
        "where is my order" (with fulfilled status)
      - Sentiment: angry, abusive, threatening language
      - Pattern: customer has sent 3+ messages on same ticket with no agent
        reply (frustration escalation)
      - Payment dispute or chargeback detected

    HIGH:
      - Order status questions ("has my order shipped?", "when will it arrive?")
      - Shipping delays
      - Product/sizing questions
      - Exchange requests
      - "Hasn't arrived" but order not yet fulfilled (pre-fulfillment delay)

    LOW:
      - Thank you messages
      - General product inquiries (no order context needed)
      - Newsletter/opt-out requests
      - Compliments
      - Simple FAQ questions with a clear KB match

  Output: priority level + classification_reason (human-readable explanation)

STEP A7: TAG THE TICKET WITH PRIORITY
  API: POST /api/tickets/{ticket_id}/tags
  Payload: {"tags": ["priority-immediate", "ai-drafted"]}
  (or "priority-high", "priority-low" accordingly)

  Safety gate: requires HERMES_ALLOW_WRITE=1
  Also adds "ai-drafted" so agents know a draft note exists.

STEP A8: SEARCH KNOWLEDGE BASE
  Issue a hybrid-search query to Supermemory (memory.<domain>) with the
  customer's message as the query. Supermemory does semantic + keyword + graph
  ranking over the ingested Markdown corpus (Git repo). See
  PHASE1_KB_ARCHITECTURE.md.

  Outcomes:
    a) MATCH FOUND -> take top N results (with source metadata)
       -> proceed to STEP A9 with KB context
    b) NO RESULT above Supermemory's relevance threshold -> set kb_gap=1
       -> proceed to STEP A8b (owner Q&A)

STEP A8b: OWNER Q&A (only when KB gap detected, for HIGH/LOW priority only)
  For Immediate priority -> skip this, go to STEP A9 with minimal draft +
  Telegram escalation (STEP A11).

  For High/Low priority with no KB match:
    1. Send Telegram message to business owner:

       "KB Gap Detected — I received a customer question I don't have
       information to answer:

       Ticket #{ticket_id}
       Customer: {name} ({email})
       Subject: {subject}
       Message: {body_text truncated to 200 chars}

       How should I respond to this? Your answer will be saved to the
       knowledge base for future similar questions."

    2. Wait for owner's reply via Telegram (async — the system stores the
       pending question and matches the next Telegram reply to it)

    3. When owner replies:
       - Write the answer as Markdown to kb/learned/owner-qa-{timestamp}.md in
         the Git repo and commit it. The ingestion worker pushes it into
         Supermemory, so the next similar question retrieves it.
       - Set draft record's kb_gap_answer = owner's reply
       - Proceed to STEP A9 with the owner's answer as KB context

    4. Timeout: if owner doesn't reply within 2 hours:
       - Post internal note: "KB gap — unable to draft. Escalated to owner.
         Customer question: {message}. Awaiting owner guidance."
       - Set status to 'no_reply' (agent will handle manually)

STEP A9: GENERATE DRAFT REPLY
  For IMMEDIATE priority:
    - Draft a brief escalation note (not a customer reply):
      "ESCALATED TO OWNER — Priority: IMMEDIATE
       Reason: {classification_reason}
       Customer message: {body_text}
       Order: #{order_number} — {financial_status} / {fulfillment_status}
       Sent Telegram notification to owner at {timestamp}."
    - Skip customer-facing draft. Post this as internal note.
    - Go to STEP A10 (save draft), then STEP A11 (Telegram notify).

  For HIGH/LOW priority:
    - Build LLM prompt with:
      a) Customer message
      b) Conversation context (last 5 messages)
      c) Order context (items, status, tracking, dates)
      d) KB results from Supermemory (the matched answers/policies)
      e) Customer profile (name, orders_count, how long they've been a customer)
      f) System instructions: tone, format, sign-off, use KB as primary source
      g) Few-shot examples from the top Supermemory results (if available)
    - Call the LLM (Ollama Cloud) to generate the draft
    - Post-process: normalize formatting, add draft metadata footer:
      "--- AI Draft | Priority: {priority} | KB: {sources} | Confidence: {score}"

STEP A10: POST DRAFT AS INTERNAL NOTE + SAVE TO DATABASE
  API: POST /api/tickets/{ticket_id}/messages
  Payload:
    {
      "channel": "internal-note",
      "via": "api",
      "from_agent": true,
      "sender": {"email": "<agent-email>"},
      "body_text": "<draft text>",
      "body_html": "<div><draft text></div>"
    }
  Safety gate: HERMES_ALLOW_WRITE=1 + dry-run first

  Database (feedback.db):
    INSERT INTO drafts (
      ticket_id, customer_message, draft_text, priority,
      classification_reason, kb_sources, kb_gap, kb_gap_question,
      kb_gap_answer, customer_email, order_context, conversation_snippet,
      model_used, confidence, status, created_at
    ) VALUES (...);

STEP A11: TELEGRAM NOTIFICATION (IMMEDIATE priority only)
  Call telegram_notify.py:
    POST https://api.telegram.org/bot{token}/sendMessage
    Payload:
      {
        "chat_id": "<owner_chat_id>",
        "text": "IMMEDIATE PRIORITY TICKET\n\n
                 Ticket #{ticket_id}\n
                 Subject: {subject}\n
                 Customer: {name} ({email})\n
                 Priority reason: {classification_reason}\n
                 Message: {body_text truncated 300 chars}\n
                 Order: #{order_number} — {status}\n\n
                 View: https://buttons-bebe.gorgias.com/tickets/{ticket_id}",
        "parse_mode": "HTML"
      }

  For HIGH/LOW priority: no Telegram notification (unless KB gap, which was
  handled in STEP A8b). The draft sits as an internal note for the agent to
  review at their own pace.

==============================================================================
WORKFLOW B — AGENT MESSAGE TRIGGER (FEEDBACK LOOP)
==============================================================================

TRIGGER: Gorgias fires "ticket.message.created" webhook with from_agent=true

STEP B0: WEBHOOK RECEIPT
  - Same receipt as STEP A0
  - Extract: ticket_id, from_agent=true, body_text, sender email, message_id

STEP B1: CHECK FROM_AGENT
  - from_agent == true -> proceed with WORKFLOW B
  - from_agent == false -> would have gone to WORKFLOW A

STEP B2: SAVE REPLY TO DATABASE
  First, dedup check:
    SELECT id FROM replies WHERE message_id = {message_id}
  If exists -> skip (already captured, webhook may have been retried)

  If new:
    INSERT INTO replies (
      ticket_id, message_id, reply_text, sender_email, channel, created_at
    ) VALUES (...);

STEP B3: LINK REPLY TO DRAFT
  Find the most recent unmatched draft for this ticket:
    SELECT id, draft_text, priority FROM drafts
    WHERE ticket_id = {ticket_id}
      AND status = 'drafted'
    ORDER BY created_at DESC LIMIT 1;

  If found:
    UPDATE drafts SET
      status = 'matched',
      matched_reply_id = {new_reply_id}
    WHERE id = {draft_id};

  If not found:
    - Agent replied without an AI draft (maybe the ticket predated the system,
      or the draft was for a different message). Just save the reply; no
      comparison needed. Could still be useful as KB material (see B5).

STEP B4: COMPUTE COMPARISON METRICS
  If a draft was matched:
    - Normalize both texts (strip whitespace, lowercase for comparison only)
    - similarity = difflib.SequenceMatcher(None, draft, reply).ratio()
    - exact_match = (normalized_draft == normalized_reply)
    - edit_ops: use difflib.SequenceMatcher.get_opcodes() to count
      additions, deletions, replacements
    - response_time = reply.created_at - draft.created_at (in seconds)

    INSERT INTO comparisons (
      draft_id, reply_id, similarity, exact_match, edit_ops,
      response_time_sec, computed_at
    ) VALUES (...);

STEP B5: UPDATE KB FROM AGENT REPLY
  This is where the KB grows from real agent behavior. The KB write path is
  always the same: write Markdown to the Git repo and let the ingestion worker
  push it into Supermemory (see PHASE1_KB_ARCHITECTURE.md). No SQLite kb_entries.

  If similarity >= 0.7 (agent largely agreed with the draft):
    - The agent's reply is a validated good answer for this question type.
    - Write it as Markdown to kb/learned/ticket-{ticket_id}.md with front-matter
      (source_type: agent_reply, ticket_id, the customer question, tags) and the
      reply text as the body. Commit it -> worker ingests -> Supermemory.
    - If a learned file for this question type already exists, update it in
      place (the Git history preserves the prior version) rather than duplicating.

  If similarity < 0.7 (agent significantly changed the draft):
    - The AI draft was off. Do NOT promote the draft.
    - Still capture the agent's reply to kb/learned/, but tag it
      review_pending: true so the owner vets it before it carries full weight.
    - Flag this comparison for the weekly review: the AI should learn why
      it was wrong (missing context? wrong tone? missing a policy detail?)

STEP B6: MARK UNMATCHED DRAFTS AS STALE
  If no agent reply comes within 7 days of a draft:
    - A daily check (could be part of the weekly cron or a separate timer):
        UPDATE drafts SET status = 'no_reply'
        WHERE status = 'drafted'
          AND created_at < datetime('now', '-7 days');
    - This means the agent didn't use the draft at all. Also worth reviewing:
      was the draft irrelevant? Was the ticket closed without reply?

==============================================================================
WORKFLOW C — WEEKLY REVIEW CRON JOB
==============================================================================

SCHEDULE: Every Monday at 9:00 AM (cron: 0 9 * * 1)

Run weekly_review.py which queries feedback.db and produces a summary:

METRICS COMPUTED:
  - Total drafts this week
  - Match rate: matched / total drafts
  - Average similarity across matched drafts
  - Exact match count (agent approved without changes)
  - Low similarity drafts (< 0.3) — AI was way off
  - No-reply drafts (agent ignored the AI suggestion)
  - Average agent response time (from draft posted to agent reply)
  - Priority distribution (how many immediate/high/low)
  - KB gaps: how many questions required owner Q&A
  - KB growth: new entries added this week (by source_type)

REPORT DELIVERED VIA TELEGRAM:
  POST https://api.telegram.org/bot{token}/sendMessage
  Payload:
    "Weekly AI Support Report — Week of {date}

     Drafts posted: {N}
     Matched by agent: {N} ({%})
     Exact matches: {N} ({%})
     Avg similarity: {score}
     Avg agent response time: {minutes}
     
     Low-similarity drafts (needs review):
     - Ticket #{id}: similarity {score} — {reason hint}
     - ...

     KB gaps (owner had to answer):
     - Ticket #{id}: {question truncated}
     - ...

     No-reply drafts (agent ignored):
     - Ticket #{id}: {customer message truncated}
     - ...

     KB entries added: {N} ({from agent replies}, {from owner Q&A})
     Total KB entries: {N}"

KB QUALITY CHECK:
  - Cross-reference low-similarity drafts (feedback.db comparisons) against the
    KB sources they cited, to spot Supermemory entries that keep producing bad
    drafts.
  - Surface kb/learned/ files tagged review_pending for the owner to vet.
  - Owner prunes/edits the offending Markdown in the Git repo; the worker
    re-syncs the change into Supermemory.

==============================================================================
API ENDPOINT SUMMARY — ALL CALLS
==============================================================================

INBOUND (Gorgias -> your server):
  POST /webhook
    - Triggers: ticket.created, ticket.message.created
    - Header: X-Webhook-Secret (validated)
    - from_agent flag routes to Workflow A (customer) or B (agent)

READ (your server -> Gorgias REST API):
  GET  /api/tickets/{ticket_id}              -- ticket metadata
  GET  /api/tickets/{ticket_id}/messages      -- full conversation
  GET  /api/customers/{customer_id}           -- customer profile
  GET  /api/customers/{customer_id}/orders    -- Shopify order data
  GET  /api/macros                             -- saved reply templates (optional KB source)

WRITE (your server -> Gorgias REST API):
  POST /api/tickets/{ticket_id}/messages      -- post internal note (the draft)
    - channel: "internal-note", public: false
    - Safety: dry-run first, then HERMES_ALLOW_WRITE=1 + --confirm
  POST /api/tickets/{ticket_id}/tags          -- add priority + ai-drafted tags
    - Safety: same gate as internal note

KB RETRIEVAL (your server -> Supermemory, behind Caddy):
  POST https://memory.<domain>/...   -- hybrid-search query at draft time
    - Auth: basic_auth (Caddy) + Supermemory bearer token
    - Returns ranked Markdown chunks + source metadata

LLM INFERENCE (your server -> Ollama Cloud):
  - Priority classification and draft generation
    (the only LLM calls; inference is offloaded off the VPS)

KB WRITES (your server / owner -> Git repo -> ingestion worker -> Supermemory):
  - Owner Q&A answer        -> commit kb/learned/owner-qa-{ts}.md
  - Validated agent reply   -> commit kb/learned/ticket-{id}.md
  - Owner manual edits      -> commit kb/policies|faq|tickets/*.md
  - Worker: git diff -> upsert/delete docs in Supermemory

EXTERNAL (your server -> Telegram):
  POST https://api.telegram.org/bot{token}/sendMessage
    - Immediate priority: ticket escalation notification
    - KB gap (High/Low): ask owner a question, await reply
    - Weekly review: summary report every Monday

LOCAL (no API, SQLite operations on feedback.db):
  INSERT INTO drafts         -- when AI posts a draft note
  INSERT INTO replies        -- when agent reply webhook arrives
  UPDATE drafts              -- link reply to draft (matched)
  INSERT INTO comparisons    -- compute similarity metrics
  (KB storage/retrieval is NOT here — it lives in Supermemory + Git.)

==============================================================================
SAFETY MODEL
==============================================================================

1. NEVER send anything to a customer automatically
   - The only Gorgias write is the internal note (channel=internal-note)
   - A human agent always reviews and decides whether to send

2. Sensitive tickets are escalated, not drafted
   - IMMEDIATE priority: no customer-facing draft, only an escalation note
   - Refunds, chargebacks, disputes: always IMMEDIATE, always escalated

3. All writes gated by HERMES_ALLOW_WRITE=1
   - Internal note post: dry-run first, then --confirm
   - Ticket tagging: same gate
   - Telegram notifications: read-only to Gorgias, but still gated to prevent spam

4. Dedup on webhook retries
   - replies table uses message_id for dedup
   - Gorgias retries on non-200; server always returns 200 to prevent loops

5. Machine-grown KB content is quarantined for review
   - Agent replies + owner Q&A land in kb/learned/ (a separate Git folder).
   - Low-similarity replies are tagged review_pending so the owner vets them
     before they carry full weight; owner Q&A is trusted (owner authored it).
   - Everything is visible and revertible in Git history.

==============================================================================
FILES TO CREATE
==============================================================================

  /root/gorgias-webhook/
    server.py              -- existing, modify handle_message_created()
    gorgias_api.py         -- existing, add tag_ticket() method
    feedback.db            -- new, SQLite (drafts/replies/comparisons only)
    feedback.db.schema.sql -- new, DB schema for setup
    draft_engine.py        -- new, drafting logic (LLM via Ollama Cloud)
    classifier.py           -- new, priority classification
    telegram_notify.py     -- new, Telegram bot integration
    kb_client.py            -- new, Supermemory query client (replaces kb_search)
    ingestion_worker.py    -- new, git diff -> push Markdown into Supermemory
    weekly_review.py       -- new, cron job for weekly summary

  Separate private Git repo (KB source of truth — see PHASE1_KB_ARCHITECTURE.md):
    kb/policies/*.md   kb/faq/*.md   kb/tickets/*.md   kb/learned/*.md

  Infra (not files in this repo):
    Supermemory (self-hosted) + SUPERMEMORY_DATA_DIR volume
    Caddy route: memory.<domain> (basic_auth + bearer)
    Ollama Cloud account (LLM inference)

==============================================================================
DATA FLOW DIAGRAM
==============================================================================

  Customer sends message in Gorgias
       |
       v
  Gorgias fires webhook (ticket.message.created, from_agent=false)
       |
       v
  server.py validates secret, parses payload
       |
       v
  +-- FETCH: ticket, messages, customer, orders (4 GET calls to Gorgias API)
       |
       v
  +-- CLASSIFY: priority (immediate / high / low)
       |
       v
  +-- TAG: POST /api/tickets/{id}/tags (priority + ai-drafted)
       |
       v
  +-- SEARCH KB: hybrid-search query to Supermemory (memory.<domain>)
       |
       +-- MATCH FOUND --> use as LLM context
       |
       +-- NO MATCH (HIGH/LOW only):
       |       |
       |       v
       |     Telegram: ask owner "how to respond?"
       |       |
       |       v
       |     Owner replies --> commit kb/learned/owner-qa-{ts}.md
       |       |                 (worker ingests -> Supermemory)
       |       v
       |     Use owner's answer as LLM context
       |
       v
  +-- DRAFT: LLM generates reply using KB + order data + conversation
       |
       v
  +-- POST NOTE: POST /api/tickets/{id}/messages (channel=internal-note)
       |
       v
  +-- SAVE DRAFT: INSERT INTO drafts (feedback.db)
       |
       +-- IMMEDIATE: Telegram notify owner with ticket details
       |
       v
  (time passes... human agent reviews draft and replies)
       |
       v
  Gorgias fires webhook (ticket.message.created, from_agent=true)
       |
       v
  server.py validates, parses, routes to Workflow B
       |
       v
  +-- SAVE REPLY: INSERT INTO replies (feedback.db)
       |
       v
  +-- LINK: find matching draft, UPDATE drafts SET status='matched'
       |
       v
  +-- COMPARE: compute similarity, edit_ops, response_time
       |          INSERT INTO comparisons
       |
       v
  +-- UPDATE KB: if similarity >= 0.7, commit agent reply to kb/learned/
       |          (worker ingests -> Supermemory)
       |
       v
  (weekly cron job runs)
       |
       v
  +-- REVIEW: query comparisons, compute aggregate metrics
       |
       v
  +-- REPORT: send summary via Telegram to owner
       |
       v
  +-- LEARN: owner reviews patterns, updates policy files / prompts / rules
       |
       v
  Next draft is better -> cycle repeats