# Release and production operations

Production is https://support.buttonsbebe.com/console/ and /inbox/ on the Hostinger VPS. Source checkout location and production installation location differ. Check the actual deployed revision before calling a branch's behavior live.

## Release path

1. Run focused checks for the changed component and the offline gate from the repo root: bash tools/verify_release.sh. It needs rg, Node, and prepared Python environments. Set PYTHON, INBOX_PYTHON, WEBHOOK_PYTHON, PROCESSOR_PYTHON, and QA_PYTHON as needed; see the script and .github/workflows/ for the current CI setup.
2. For AI behavior changes, use testing/HOW-TO-RUN.md and TESTING-READINESS.md for the 48-scenario live-model gate. That is distinct from unit and syntax checks.
3. A successful push to main triggers .github/workflows/deploy-production.yml after verify. Follow deploy/cd/README.md before any release. The receiver inventories only allowlisted source, checks approved Caddy/systemd fingerprints, and stops/restarts affected services. It does not install dependencies, copy secrets, deploy KB content, or roll back databases.

deploy/cd/source_release.py is the current deploy inventory. Inbox backend files go to /opt/buttonsbebe/inbox2/; its five public assets go to /var/www/inbox2/; shared modules remain under /opt/buttonsbebe/inbox/console-src/inbox/; state remains in /var/lib/buttonsbebe-inbox2*. A change to deploy/cd/ is special: the privileged receiver and helper are installed manually together under the runbook before an auto-deploying main merge. Dependency manifest changes also require a prepared environment switch first.

deploy/caddy/sites/support.caddy is a redacted route source, not a credential bundle. The applied Caddy file and systemd units are fingerprint-gated. Preserve the authenticated /inbox/ and /console/ routes and the deny rule for public /dashboard/*. Do not bypass the receiver by copying source files or restoring a whole old tree over runtime state.

## Read-only production checks

On the VPS, these commands inspect the current processes without changing tickets:

    systemctl status helpdesk-inbox2 buttonsbebe-inbox2-shop buttonsbebe-webhook
    systemctl status buttonsbebe-inbox-projection.timer buttonsbebe-gorgias-mcp
    curl -fsS http://127.0.0.1:8767/health
    curl -fsS http://127.0.0.1:8000/ready

/inbox/ and /console/ require a signed-in console session. The Inbox API's own /health only confirms its local process; release readiness also checks fresh projection and locked Send behavior. For deployment failures and recovery, use deploy/cd/README.md and deploy/PRODUCTION-OPERATOR-RUNBOOK.md; source rollback does not reverse database migrations or customer actions.

## Sensitive boundaries to retain

- Gorgias, Redo, and Shopify reads are separate from the human-confirmed console Send/Note path. No background assistant or Inbox API may silently send a reply.
- The Inbox service has no provider credentials and can connect only to localhost. The Shopify worker is separate because its protected credentials and external network access have a different trust level.
- The root .env, runtime DBs, logs, WhatsApp session, KB index, and learned/customer data do not belong in Git, skill assets, previews, or release archives. See AGENTS.md and deploy/ENV-CONSOLIDATION-RUNBOOK.md for the current credential layout.
