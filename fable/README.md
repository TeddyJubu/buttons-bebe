# Fable — the Buttons Bebe help desk (Gorgias replacement)

> Plain-English guide. Built 2026-07-10 on branch `Fable_buttonsbebe`.
> Nothing here touches the live VPS, the real Gorgias account, or the real Shopify store — ever.

## What this is

Fable is a complete help desk that runs on your own computer. Customer messages from
**email, website chat, and WhatsApp** all land in one inbox. For every message, the AI
writes a **suggested reply** — but it never sends anything. A human reads the draft and
clicks **Send to customer** (with a confirm step). Tickets that mention refunds, damage,
or angry customers get an amber **"Needs a careful look"** warning.

To test safely, Fable ships with three **emulators** — little pretend versions of the real
services that speak their exact language:

| Pretend service | What it does | Port |
|---|---|---|
| Shopify emulator | Answers exactly like the real Shopify API — fake store with 30 baby-clothing products, 25 customers, 40 orders (#BB1001–#BB1040) | 9601 |
| Redo emulator | Answers returns/refunds questions like the real Redo API | 9602 |
| Mailbox emulator | Catches every outgoing email in a box you can inspect — so no email can ever really leave your machine | 9603 |
| **Fable itself** | The help desk + the screens you use | **9600** |

## Try it (one command)

```bash
cd fable
./scripts/demo.sh
```

This starts everything, plays a little story (Emma emails "Where is my order #BB1015?",
someone asks about shipping on chat, an upset WhatsApp message gets flagged), and leaves
the app running. Then open **http://127.0.0.1:9600** in your browser.

- The chat widget demo store page: **http://127.0.0.1:9600/widget/demo-store.html**
- Stop everything: `./scripts/stop-server.sh && ./emulators/stop-emulators.sh`

## Check that everything works (one command)

```bash
./fable/scripts/test.sh          # 182 automated checks + coverage report
FABLE_E2E=1 ./fable/scripts/test.sh   # also runs the full live end-to-end story
```

## The safety rules (same as before, tested automatically)

1. The AI **never** sends anything by itself — it only drafts.
2. Nothing leaves your computer — all connections go to the local emulators; outgoing
   email is trapped in the mailbox emulator's outbox.
3. Refund/damage/angry messages are always flagged for a careful human look.
4. Every action is written to an audit log.

The test suite (`tests/integration/test_safety_invariants.py`) proves all four on every run.

## Folder map

```
fable/
├── server/        the help desk engine (tickets, AI pipeline, audit) — port 9600
├── console/       the screens you use (same purple design system as the old dashboard)
├── widget/        the website chat bubble + a demo store page
├── emulators/     shopify / redo / mailbox pretend services
├── tests/         186 automated checks (unit / integration / end-to-end)
├── scripts/       run-all.sh · demo.sh · test.sh · stop scripts
└── docs/          SPRINT-PLAN.md · API-CONTRACT.md · TESTING-STRATEGY.md · research
```

## Swapping fake for real (later — Sprint 2)

Everything fake is a plug, not a rewrite:

- **Real Shopify:** change `SHOPIFY_BASE` in `fable/.env.fable` to the real store URL and put
  in the real client id/secret. Same code path — the emulator speaks the identical API.
- **Real AI:** set `FABLE_BRAIN=anthropic` (or `hermes`) and finish the adapter stub in
  `server/app/brains/` — the mock and real brains share one interface.
- **Real email / WhatsApp:** swap the mailbox emulator for an IMAP/SMTP adapter, and the
  emulated WhatsApp channel for the existing `whatsapp-connect` bridge.
- **Migration from Gorgias:** planned importer (docs/SPRINT-PLAN.md P2) — reads the Gorgias
  export API, writes into Fable with original ids preserved. Tested against an emulator first.

## For the old tools

Fable answers the same five API calls the existing `tools/gorgias_mcp.py` makes to Gorgias
(`/api/tickets`, `/api/tickets/{id}`, messages, customers). Point that tool's base URL at
`http://127.0.0.1:9600` and it works against Fable unchanged.
