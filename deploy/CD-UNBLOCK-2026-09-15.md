> Archived incident record (2026-09-15 CD unblock). The procedure below is
> historical; the live approval procedure is PRODUCTION-OPERATOR-RUNBOOK.md.

# Unblock CD: manual deploy/systemd + deploy/caddy approval (2026-09-15)

CD refuses releases with exit 78 (`Deployment configuration 'deploy/systemd' changed;
deploy it manually before CD can continue`) when the `deploy/systemd` or `deploy/caddy`
source trees drift from the pins in `/etc/buttonsbebe-deploy-approved-config.sha256`.
This is the safety gate working as designed: infra changes need human review.
No release in this state (including `d8f7f5a`) can deploy until an operator approves.

## 1. Review (laptop, no risk)

```bash
git log --oneline -5 -- deploy/systemd deploy/caddy
mkdir -p /tmp/relcheck && git archive d8f7f5a deploy/systemd deploy/caddy | tar -x -C /tmp/relcheck
```

Diff each live unit against the release candidate once on the box (phase 3).

## 2. Back up live config (VPS, usual admin SSH login)

```bash
sudo cp -a /etc/buttonsbebe-deploy-approved-config.sha256 /root/buttonsbebe-deploy-approved-config.sha256.bak-20260915
sudo mkdir -p /root/cfg-bak-20260915 && sudo cp -a /etc/systemd/system/helpdesk-inbox.service /etc/systemd/system/buttonsbebe-inbox-projection.service /etc/systemd/system/buttonsbebe-inbox-projection.timer /etc/caddy/sites/support.caddy /root/cfg-bak-20260915/
cat /etc/buttonsbebe-deploy-approved-config.sha256
```

## 3. Install reviewed files

Copy `/tmp/relcheck/deploy/systemd/*` to the VPS, diff every file against live,
install only accepted units. For Caddy, never copy blindly: the repo file is a
redacted template with placeholders, so merge reviewed changes into the live file.

```bash
sudo cp /tmp/relcheck/deploy/systemd/<accepted-unit> /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/<accepted-unit>
sudo systemctl daemon-reload
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
sudo systemctl restart <each changed unit>
systemctl is-active buttonsbebe-webhook helpdesk-inbox
curl -s http://127.0.0.1:8000/ready; echo
curl -s http://127.0.0.1:8766/ready; echo
curl -s -X POST http://127.0.0.1:8766/console/api/helpdesk -H 'Content-Type: application/json' -d '{"tool":"helpdesk.send_reply","arguments":{"ticketId":"t-check","text":"hi","confirmed":true}}'; echo
```

Expect `{"ok":true...}` on both readys and the exact Send lock
(`send_access_inactive` / `Activate the send access.`). On any failure, stop and
restore from `/root/cfg-bak-20260915`.

## 4. Record approvals (VPS, only after phase 3 verifies)

```bash
cd /tmp/relcheck/deploy/systemd && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum
cd /tmp/relcheck/deploy/caddy && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum
sudo sha256sum /etc/caddy/sites/support.caddy /etc/systemd/system/helpdesk-inbox.service /etc/systemd/system/buttonsbebe-inbox-projection.service /etc/systemd/system/buttonsbebe-inbox-projection.timer
```

As root, set the `deploy/systemd` and `deploy/caddy` lines plus the `/etc/...`
applied lines in `/etc/buttonsbebe-deploy-approved-config.sha256` to these hashes.
Preserve every other line. Do both dirs in one pass (CD checks caddy next).

## 5. Retry and confirm (laptop)

```bash
gh run rerun 34965409670
```

CD stages the release, pauses the projection timer during the switch, runs the
oneshot export, and enforces inbox `/ready` plus the Send lock. Confirm live:
production `/ready`, channel badges/status on real tickets, projection age < 180s.

## Notes

- Release `d8f7f5a` needs no manual DB step: `init_db` adds `ticket_status` itself.
- Never restore the whole app tree or a stale DB as a rollback; use the
  receiver journal (`deploy/cd/README.md`, `deploy/PRODUCTION-OPERATOR-RUNBOOK.md`).
