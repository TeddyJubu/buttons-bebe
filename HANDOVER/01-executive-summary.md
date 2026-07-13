# 01 · Executive Summary

*What this doc covers: the product, who it's for, its current status, the two codebases, and the five facts a newcomer most needs.*

*Sources: `CLAUDE.md`, the Phase 2/3 plan deck, and the verified findings in docs 02–09.*

---

## What this project is

An **AI customer‑support agent for Buttons Bebe**, an e‑commerce store that runs on **Shopify** and handles customer support in **Gorgias** (a help‑desk product). The store receives roughly **2,000 support tickets per month**.

For every incoming ticket, the agent:

1. **Reads** the customer's message in context.
2. **Pulls order context** — order, return, and product details — automatically.
3. **Searches a knowledge base** of policies, FAQs, ~22 support "intents", and ~4,246 live products.
4. **Classifies** the ticket's risk.
5. **Drafts a reply** and posts it as a **private internal note** in Gorgias for a human to review, edit, and send — **or escalates** sensitive tickets to a human.

The client is **Chaim**. The builder handing this over is **Tony**.

## The safety model (the most important thing)

The entire system is built around one promise:

> **The AI never sends a message to a customer on its own. Every customer‑facing reply is written by the AI but *sent by a human*. Sensitive tickets (refunds, disputes, damaged/wrong items, angry customers) are flagged, never auto‑handled.**

Concretely: the only thing the AI writes back to any external system is a **staff‑only internal note in Gorgias**. All other external access (Shopify, Redo returns, Gorgias reads) is **read‑only**. Preserve this model in every change. The full five‑rule version is in doc `02` and `CLAUDE.md §2`.

## Current status: Phase 1 (Copilot) is LIVE

The agent runs in production today on a VPS. It works as a **copilot**: it drafts, a human approves and sends. What's live and verified: the webhook→queue→processor loop, the Hermes "brain" running per ticket with three read‑only tools, hybrid knowledge‑base search including the auto‑synced product catalog, Gorgias read + internal‑note write, WhatsApp escalation alerts, an owner "Notice Board" for on‑the‑fly overrides, and a learning loop that captures the human's real replies to improve future drafts. A few pieces are still **stubs** (notably the deterministic risk classifier — risk is currently judged by the AI itself). See doc `06` for the precise LIVE‑vs‑stub breakdown.

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

**Phase 2 (~4–6 weeks): "Trustworthy & Visible"** — add a deterministic safety net (the classifier), clean up drafts, stop the AI inventing prices/policies, finish the live Shopify order/tracking integration, turn the learning loop fully on, and build an **owner dashboard** (draft‑acceptance rate, tickets handled, hours saved, top topics). A stretch goal is an **auto‑send pilot for one very low‑risk topic** (order status) behind confidence checks and a kill switch. **Phase 3 (~3–4 months): "Autonomous & Multi‑channel"** — graduate auto‑send to more safe topics, add channels (web chat, Instagram/Facebook DMs, SMS/WhatsApp), and let the AI *take actions* (refunds, returns, discounts) via new **gated** writes. Full program: ~4.5–6 months. Doc `08` has the feature‑by‑feature breakdown and the **5 policy questions the client must answer** to make the AI accurate.

## The five facts that matter most

1. **The repo is incomplete on its own.** The live `webhook/` and `processor/` services and the `~/.hermes/` brain config are **not in the repo** — they're only on the server. Pull them (read‑only) using the procedure in doc `06` before you try to run anything.
2. **The safety model is sacred.** AI drafts; humans send; sensitive tickets escalate; the only external write is a Gorgias internal note.
3. **The live system is Hermes on `main`.** `glm-5.2` via Ollama Cloud, guided by `SOUL.md` + a Hermes skill, using three read‑only MCP tools (knowledge base :8077, Redo returns :8078, Gorgias :8079). The webhook receiver + dashboard run on :8000.
4. **There's a second, offline codebase (Fable) on another branch** you must make a decision about.
5. **A few security/cleanup items need day‑one attention** — the secured WhatsApp configuration is fixed in the repository but still needs coordinated VPS rollout and secret rotation; a populated `.env` remains on disk (git‑ignored), and two `.env` files still need consolidation. Doc `06` has the checklist.

## Where the system runs

Production VPS **`srv1766050`** (IP `2.25.137.77`), Ubuntu. Everything lives under `/root/Buttonsbebe Agent/`. Services are managed by **systemd** and fronted by **Caddy** (HTTPS). Full inventory of ports, services, and credentials is in doc `05`.
