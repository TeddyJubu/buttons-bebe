# 01 · Executive Summary

> ⚠️ **SUPERSEDED.** This historical summary is not an operating source of
> truth. Use the repository-root `AGENTS.md` and `CLAUDE.md` for the current
> architecture and safety model.

*What this doc covers: the product, who it's for, its current status, the two codebases, and the five facts a newcomer most needs.*

*Sources: `CLAUDE.md`, the Phase 2/3 plan deck, and the verified findings in docs 02–09.*

---

## What this project is

An **AI customer‑support agent for Buttons Bebe**, an e‑commerce store that runs on **Shopify** and handles customer support in **Gorgias** (a help‑desk product). The store receives roughly **2,000 support tickets per month**.

For every incoming ticket, the agent:

1. **Reads** the customer's message in context.
2. **Pulls order context** — order, return, and product details — automatically.
3. **Searches a knowledge base** of policies, FAQs, 22 support "intents", and
   4,018 active Shopify products.
4. **Classifies** the ticket's risk.
5. **Creates a console draft for every ticket.** Sensitive drafts are clearly
   prefixed and raised for human review; they are not suppressed. A human can
   edit, send, post an internal note, request a rewrite, or discard the draft.

The client is **Chaim**. The builder handing this over is **Tony**.

## The safety model (the most important thing)

The entire system is built around one promise:

> **The AI never sends a message to a customer on its own. Every customer‑facing reply is written by the AI but *sent by a human*. Sensitive tickets (refunds, disputes, damaged/wrong items, angry customers) are flagged, never auto‑handled.**

Concretely: Hermes and all three MCP tools are read-only. The AI does not post
internal notes. Gorgias writes occur only after a human uses the console's
**Send reply** or **internal Note** action; public send also requires
confirmation. Shopify and Redo remain read-only. Preserve this model in every
change. The current rules are in root `AGENTS.md` and `CLAUDE.md`.

## Current status: Phase 1 (Copilot) is LIVE

The agent runs in production today on a VPS. It works as a **copilot**: it
creates console drafts and a human chooses the final action. Live components
include the webhook→queue→processor loop, Hermes with three read-only tools,
hybrid KB search over the auto-synced 4,018-product catalog, human-triggered
Gorgias actions, WhatsApp escalation alerts, the Notice Board, and the learning
loop. The deterministic classifier is implemented as an advisory, escalation-
only safety net; Hermes also classifies risk.

## Two codebases (this trips people up)

| | **`main` branch — "Hermes"** | **`Fable_buttonsbebe` branch — "Fable"** |
|---|---|---|
| Status | **LIVE in production** | **Not deployed** (offline dev/test track) |
| Brain | Hermes Agent CLI running model `glm-5.2` via Ollama Cloud | Pluggable "brains" (mock / Anthropic / Hermes‑stub) behind one interface |
| Talks to | The real Gorgias, Shopify, Redo | Local **emulators** of Gorgias/Shopify/Redo/mailbox — runs fully offline |
| Tests | Manual + QA runs | A real automated suite (unit/integration/e2e, golden set, safety‑invariant tests) |
| Why it exists | It's the working product | A cleaner, testable rebuild aimed at the Phase 2 hardening goals |

**You will need to decide** whether to keep evolving `main`, adopt Fable, or merge the best of each. Note Fable is *not* a strict superset — the Notice Board feature and the current console live only on `main`. Doc `07` and `09` have the details.

## The roadmap in one paragraph

The roadmap in this package is historical and must be re-approved before use.
Current work should preserve the human approval gate, strengthen the advisory
classifier and grounding, and improve observability without adding autonomous
send or external write capabilities.

## The five facts that matter most

1. **The runtime source is reviewable in the repo.** `webhook/`, `processor/`,
   and scrubbed Hermes skills are present. Secrets, queue data, and derived KB
   artifacts remain deployment-only.
2. **The safety model is sacred.** AI drafts; humans decide every Gorgias write;
   sensitive tickets receive a prefixed draft and elevated review.
3. **The live system is Hermes on `main`.** `glm-5.2` via Ollama Cloud, guided by `SOUL.md` + a Hermes skill, using three read‑only MCP tools (knowledge base :8077, Redo returns :8078, Gorgias :8079). The webhook receiver + dashboard run on :8000.
4. **There's a second, offline codebase (Fable) on another branch** you must make a decision about.
5. **Current limitations are listed in `CLAUDE.md`.** They include split runtime
   environment files, the advisory classifier, a retired poll-based feedback
   collector, and Hermes `--yolo` requiring a strictly read-only tool set.

## Where the system runs

Production VPS **`srv1766050`** (IP `2.25.137.77`), Ubuntu. Everything lives under `/root/Buttonsbebe Agent/`. Services are managed by **systemd** and fronted by **Caddy** (HTTPS). Full inventory of ports, services, and credentials is in doc `05`.
