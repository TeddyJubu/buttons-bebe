# Buttons Bebe AI Support Agent — Developer Handover

> ⚠️ **SUPERSEDED — DO NOT USE THIS FOLDER AS CURRENT OPERATING OR
> ARCHITECTURE DOCUMENTATION.** The `HANDOVER/` package is a historical snapshot
> from 2026-07-13 and contains details that no longer match production. Use the
> repository-root `AGENTS.md` and `CLAUDE.md` for the live system as of
> 2026-07-14. Read files in this folder only for historical context, and verify
> every claim against those current sources before acting on it.

This folder was prepared as a self-contained handover package for the team
taking over the Buttons Bebe AI support agent. It is retained for historical
context, not as a runbook.

> **Prepared:** 2026‑07‑13 · **Handover from:** Tony (builder) → client's development team · **Client:** Chaim (Buttons Bebe, a Shopify store)

---

## 30‑second orientation

Buttons Bebe gets ~2,000 customer support tickets/month in **Gorgias** (their
help desk). The agent reads each ticket, gathers order/return/product context,
searches a knowledge base, and creates a first-draft reply in the support
console. It does not post that draft to Gorgias.

**The core safety rule: the AI never sends anything to a customer on its own. A human always sends.** Keep this rule in mind for every change you make — it is the promise the whole system is built around.

The system is **live in production today** (this is "Phase 1 — Copilot"). There is a written roadmap for Phase 2 (hardening + a dashboard) and Phase 3 (more autonomy + more channels) — see doc `08`.

---

## ⚠️ Three things to know before you do anything

1. **The runtime source is now in this repo.** The webhook receiver (`webhook/`),
   processor/orchestrator (`processor/`), and scrubbed Hermes skills (`hermes/`)
   are reviewable here. Runtime secrets, queue data, the derived Shopify product
   corpus, and the built LanceDB index remain deployment artifacts and must not
   be committed.

2. **There are two branches, and they are different systems.**
   - **`main`** — the **live** system running in production (the "Hermes" agent). This is what serves real customers today.
   - **`Fable_buttonsbebe`** — a **newer, offline‑testable rebuild** ("Fable") that is **not deployed anywhere**. It's a parallel track with its own test suite and local emulators. Doc `07` covers it. You'll need to decide whether to continue it, fold it into `main`, or shelve it.

3. **Do not change the production server as part of onboarding.** The handover instruction was explicit: **no changes on the VPS.** Copying files *off* the server (read‑only) is fine and expected; deploying, restarting, or editing anything on it is not — until you own the system and decide to.

---

## How to read this handover (suggested order)

| # | Doc | What it gives you |
|---|-----|-------------------|
| — | **`README.md`** (this file) | Orientation + reading order |
| 01 | **`01-executive-summary.md`** | The product, current status, and the 5 facts that matter most |
| 02 | **`02-live-architecture.md`** | How the live system works end‑to‑end (the big picture + flow diagram) |
| 03 | **`03-live-components-reference.md`** | A "read the actual code" tour of every in‑repo module |
| 04 | **`04-knowledge-base-and-learning.md`** | The knowledge base, search engine, product sync, notices, and learning loop |
| 05 | **`05-services-deploy-and-secrets.md`** | Ports, services, credentials inventory, and the operate/verify runbook |
| 06 | **`06-known-issues-and-completeness.md`** | ⭐ Bugs, stubs, doc drift, **what's missing from this repo, and how to pull it off the server** |
| 07 | **`07-fable-rebuild.md`** | The Fable offline rebuild (the other branch) |
| 08 | **`08-phase-2-3-roadmap.md`** | The client‑facing roadmap, mapped to real files/stubs |
| 09 | **`09-repository-map.md`** | Every folder and file explained; current vs retired; both branches |
| 10 | **`10-github-and-onboarding.md`** | Git/branch setup, pushing to GitHub, and a first‑week checklist |
| 11 | **`11-glossary.md`** | Plain‑English definitions of every term and tool used here |

**If you're an AI agent:** do not use this reading order for current work. Read
the repository-root `AGENTS.md` and `CLAUDE.md`; consult this folder only when a
task explicitly requires historical handover context.

---

## The single source of truth

The repository-root **`AGENTS.md`** and **`CLAUDE.md`** are the authoritative
maps for the live system. This handover no longer expands or verifies them. If
anything in `HANDOVER/` differs, the handover is wrong.

---

## What "done" looks like for your onboarding

You are ready to take over when you can:

1. Clone the repo and read root `AGENTS.md` and `CLAUDE.md`.
2. Review the in-repo runtime source in `webhook/`, `processor/`, and `hermes/`.
3. Run the current repository release gate and the live verification commands
   documented in `CLAUDE.md`.
4. Treat credentials and production data as deployment-only secrets.
5. Consult this handover only for explicitly historical questions.

Current operating instructions are outside this superseded folder.
