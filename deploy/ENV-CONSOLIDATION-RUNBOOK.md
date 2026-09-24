# Runbook — one `.env`, and rotate the exposed secrets

**Status: not started on the VPS. This document is the plan, not a record.**

Task 6 of the Fable port. Everything here is a **manual step you run on the
server**. Nothing in this repo performs it, and nothing in this repo touches a
live credential. Read the whole thing once before you start.

> **Do this in a quiet hour, one service at a time, with the Task 2 heartbeat
> already running.** This is the highest-blast-radius item in the port: a
> single missing variable takes services down at restart.

---

## Part 0 — What is already done (do not redo it)

Checked against the current repo on 2026-07-29:

| Claim in the old plan | Reality now |
|---|---|
| "processor and webhook read different `.env` files" | **Already fixed in code.** Both `processor/config.py` and `webhook/src/bb_webhook/config.py` load the single `/root/Buttonsbebe Agent/.env` (consolidated 2026-07-08). |
| "delete the dead `SHOPIFY_ADMIN_API_TOKEN`" | **Already gone** from both config files. Only `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET` remain. Some docs still mention it — that is stale text, not live code. |
| "secrets may be in git history" | **They are not.** A full-history scan for Shopify (`shpat_`, `shpca_`, `shpss_`), OpenRouter (`sk-or-v1-`), Slack (`xoxb-`), GitHub (`ghp_`) and AWS (`AKIA`) token prefixes found only redaction patterns and placeholder examples. No `.env` file has ever been committed. `_VPS-FULL-BACKUP-*/` is gitignored and was never committed. |

So the remaining work is **on the VPS only**, plus rotating the credentials
that are sitting in plaintext in the backup folder on disk.

---

## Part 1 — Before you touch anything

```bash
ssh root@<vps>
cd "/root/Buttonsbebe Agent"

# 1. Snapshot both env files OUTSIDE the repo, with the date in the name.
mkdir -p /root/env-snapshots
cp .env                 /root/env-snapshots/main.env.$(date +%F)
cp webhook/.env         /root/env-snapshots/webhook.env.$(date +%F) 2>/dev/null || \
  echo "no webhook/.env — the split may already be resolved"
chmod 600 /root/env-snapshots/*

# 2. Record which services are healthy RIGHT NOW, so you can compare after.
systemctl is-active buttonsbebe-webhook buttonsbebe-processor \
  buttonsbebe-kb-mcp buttonsbebe-redo-mcp buttonsbebe-gorgias-mcp \
  buttonsbebe-whatsapp-connect buttonsbebe-kb-admin | tee /root/env-snapshots/before.txt

# 3. Record where each unit gets its environment from.
for u in webhook processor kb-mcp redo-mcp gorgias-mcp whatsapp-connect kb-admin; do
  echo "--- buttonsbebe-$u"; systemctl cat "buttonsbebe-$u" | grep -E 'EnvironmentFile|Environment=' || true
done | tee /root/env-snapshots/env-sources.txt
```

Read `env-sources.txt` carefully. **Any `EnvironmentFile=` still pointing at
`webhook/.env` is a service that will break when you delete that file.**

---

## Part 2 — Merge

```bash
cd "/root/Buttonsbebe Agent"

# Which keys exist only in webhook/.env?
comm -23 <(grep -oE '^[A-Z_][A-Z0-9_]*' webhook/.env | sort -u) \
         <(grep -oE '^[A-Z_][A-Z0-9_]*' .env         | sort -u)
```

For each key that appears:

1. If it is genuinely needed, append it to the main `.env`.
2. If the same key exists in both **with different values**, the main `.env`
   wins (that is the precedence the loaders already use) — but stop and work
   out which value is actually correct before moving on. A silently wrong
   Gorgias key is exactly the failure this consolidation is meant to end.
3. If nothing uses it, drop it. Grep first:
   `grep -rn "THE_KEY" processor webhook kb tools feedback whatsapp-connect`

Then lock the file down and remove the duplicate:

```bash
chmod 600 "/root/Buttonsbebe Agent/.env"
mv webhook/.env /root/env-snapshots/webhook.env.retired.$(date +%F)
```

Do **not** delete the snapshot until every service has been green for 24 hours.

---

## Part 3 — Restart, one at a time

Order matters: dependencies first, the processor last.

```bash
for u in buttonsbebe-kb-mcp buttonsbebe-redo-mcp buttonsbebe-gorgias-mcp \
         buttonsbebe-whatsapp-connect buttonsbebe-kb-admin \
         buttonsbebe-webhook buttonsbebe-processor; do
  echo "=== $u"
  systemctl restart "$u"
  sleep 5
  systemctl is-active "$u" || { echo "STOP — $u did not come back"; journalctl -u "$u" -n 40 --no-pager; break; }
done
```

If one fails: restore the snapshot (`cp /root/env-snapshots/main.env.<date> .env`),
restart that service, and work out the missing key before continuing.

### Acceptance checks

```bash
systemctl is-active buttonsbebe-{webhook,processor,kb-mcp,redo-mcp,gorgias-mcp,whatsapp-connect,kb-admin}
hermes mcp test buttonsbebe_kb                     # KB tool still answers
bash tools/verify_hermes_toolset.sh                # Task 5's read-only check
journalctl -u buttonsbebe-processor -n 30 --no-pager | grep -i "idle heartbeat\|starting"
```

Then send **one** real ticket through end to end and confirm a draft appears in
the console. A human still clicks send — that never changes.

---

## Part 4 — Rotate the exposed credentials

`_VPS-FULL-BACKUP-20260706/secrets/root.env` holds **live secrets in
plaintext** on disk. It is gitignored and was never committed, so this is a
local-disk exposure, not a public one — but it has been copied around, so
treat every value in it as burned.

Rotate in this order. Each is independent; do one, verify, then the next.

| # | Credential | Where to rotate | What to update afterwards |
|---|---|---|---|
| 1 | `WEBHOOK_SECRET` | Generate: `openssl rand -hex 32` | Main `.env`, **and** the Gorgias webhook integration's shared secret. Rotate both within the same minute or webhooks bounce. |
| 2 | `GORGIAS_API_KEY` | Gorgias → Settings → REST API | Main `.env`. Restart webhook + processor + gorgias-mcp. |
| 3 | `SHOPIFY_CLIENT_SECRET` (+ `SHOPIFY_CLIENT_ID` if reissued) | Shopify admin → the custom app's API credentials | Main `.env`. Restart the KB sync job and re-run one product sync. |
| 4 | `REDO_API_KEY` | Redo dashboard | Main `.env`. Restart redo-mcp, then `hermes mcp test buttonsbebe_redo`. |
| 5 | `WA_TOKEN`, `WA_PASSWORD`, `WA_SEND_SECRET` | Choose new random values | `deploy/systemd/*.service.d/*.conf` drop-ins **on the VPS** (the copies in this repo are `<REDACTED>` templates — keep them that way). Restart whatsapp-connect, processor and heartbeat, then force one alert to prove delivery still works. |
| 6 | LLM / OpenRouter / Fireworks keys | The provider dashboard | `~/.hermes/config.yaml` and/or main `.env`. Run one ticket end to end. |
| 7 | Postgres / Telegram, if still present in the backup | The respective service | Only if anything still uses them — grep first. |

After all of them:

```bash
# Verify nothing you rotated still appears anywhere on disk outside the .env.
grep -rl "<the OLD value>" /root --exclude-dir=env-snapshots 2>/dev/null

# Then get rid of the plaintext backup.
tar -czf /root/vps-backup-20260706.tar.gz "_VPS-FULL-BACKUP-20260706"
gpg -c /root/vps-backup-20260706.tar.gz && rm /root/vps-backup-20260706.tar.gz
rm -rf "_VPS-FULL-BACKUP-20260706"
```

(Or simply delete it — you have git for the code, and the secrets in it are
dead once rotated.)

---

## Part 5 — Close it out in the docs

Only after every service has been green for 24 hours — and sweep *every*
remaining reference, not just the obvious ones:

- Remove the legacy-`webhook/.env` bullet from **`AGENTS.md` §7**.
- Update `AGENTS.md` to list one `.env`, not two — including any later-added
  paragraph elsewhere in the file that still describes the two-file split.
- Note the same in `HANDOVER/02-live-architecture.md` §271 (historical doc —
  annotate, don't rewrite).
- Note the rotation date in `archive/DEV-ISSUES.md` #9 and mark it resolved.
- Update the deploy-file entry that still records the legacy split (this
  runbook's own §"What people say" row and snapshot/rollback blocks above are
  procedure, not status — leave those; they describe the transition).
- Grep the repo for `webhook/.env` afterwards; any hit outside this runbook's
  procedure sections and historical archives means the close-out is
  incomplete.

---

## Rollback

At any point:

```bash
cp /root/env-snapshots/main.env.<date> "/root/Buttonsbebe Agent/.env"
cp /root/env-snapshots/webhook.env.<date> "/root/Buttonsbebe Agent/webhook/.env"
chmod 600 "/root/Buttonsbebe Agent/.env" "/root/Buttonsbebe Agent/webhook/.env"
systemctl restart buttonsbebe-webhook buttonsbebe-processor
```

Rotated keys cannot be un-rotated — that is why rotation is Part 4, after the
merge is proven stable, and why each one is done separately.
