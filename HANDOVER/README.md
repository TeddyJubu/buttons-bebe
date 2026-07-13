# Buttons Bebe AI Support Agent — Developer Handover

**Read this first.** This folder is a complete, self-contained handover package for the team taking over the Buttons Bebe AI support agent. It is written for **both a human developer and an AI coding agent** with zero prior context. If you just cloned this repo from GitHub, start here.

> **Prepared:** 2026‑07‑13 · **Handover from:** Tony (builder) → client's development team · **Client:** Chaim (Buttons Bebe, a Shopify store)

---

## 30‑second orientation

Buttons Bebe gets ~2,000 customer support tickets/month in **Gorgias** (their help desk). This project is an **AI agent that reads each ticket, gathers order/return/product context, searches a knowledge base, and writes a first‑draft reply** — which it posts as a **private internal note** for a human to review and send.

**The core safety rule: the AI never sends anything to a customer on its own. A human always sends.** Keep this rule in mind for every change you make — it is the promise the whole system is built around.

The system is **live in production today** (this is "Phase 1 — Copilot"). There is a written roadmap for Phase 2 (hardening + a dashboard) and Phase 3 (more autonomy + more channels) — see doc `08`.

---

## ⚠️ Three things to know before you do anything

1. **This repo is a *partial* snapshot — cloning GitHub alone does not give you a runnable system.** Two core services that run in production — the **webhook receiver** (`webhook/`) and the **processor/orchestrator** (`processor/`), plus the **Hermes brain config** (`~/.hermes/`: `config.yaml`, `SOUL.md`, the `buttonsbebe` skill) — have **no source in this repo**. They live only on the production server. **Doc `06` explains exactly what's missing and gives a safe, read‑only procedure to copy it off the server** so the repo becomes complete. Do this early.

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

**If you're an AI agent:** read `01`, then `02`, then `06` (so you understand what's *not* here before you try to run anything), then the rest as needed. Also read the repo‑root **`CLAUDE.md`** — it is the current in‑repo source of truth for the live architecture. Note that a few older docs in the repo describe a *retired* design; `06` lists exactly which files not to trust.

---

## The single source of truth

Inside the repo root, **`CLAUDE.md`** is the authoritative, current architecture map for the **live** system. This handover expands on it, verifies it against the actual files, and records where it has drifted (see `06`). Where this handover and an older doc disagree, trust this handover and `CLAUDE.md`.

---

## What "done" looks like for your onboarding

You are ready to take over when you can:

1. Clone the repo and read this handover.
2. Complete the repo by pulling the VPS‑only source (doc `06`).
3. Reproduce the live architecture in your head from doc `02`.
4. Run the verify commands in doc `05` against the server (read‑only) and see green.
5. Locate the credentials you'll need and know how to rotate them (docs `05` + `06`).
6. Decide the fate of the Fable branch (doc `07`) and lock Phase 2 scope (doc `08`).

Everything you need to do each of these is in the docs above.
