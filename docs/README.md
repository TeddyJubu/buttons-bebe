# Current and historical documents

Use [AGENTS.md](../AGENTS.md) for safety rules and the current component map.
A dated report describes its recorded source or runtime. It does not prove a
change is installed today.

## Current instructions

- [Repository overview](../README.md) explains ticket intake, AI drafts and human replies.
- [Active Inbox](../console-src/inbox2/README.md) describes `/inbox/`, its current services and reply controls.
- [Shared data modules](../console-src/inbox/PRODUCTION.md) describes projection and Shopify dependencies.
- [Development](../skills/buttonsbebe-support-webapp/references/development.md) covers synthetic local preview and component checks.
- [Source release and recovery](../deploy/cd/README.md) covers journaled source changes on a provisioned host.
- [Operator runbook](../deploy/PRODUCTION-OPERATOR-RUNBOOK.md) covers reviewed production checks and recovery boundaries.
- [AI reply reliability](AI-REPLY-RELIABILITY.md) describes generation, review and durable retries.
- [Local monitoring](../deploy/LOCAL-MONITOR.md) explains process liveness, usable ticket data and worker progress.
- [Knowledge-search release](../kb/SEARCH-OUTCOME-RELEASE.md) separates local response-shape changes from future installed instructions.
- [Model-quality evaluation](../testing/HOW-TO-RUN.md) describes a separate live-model run before behavior-changing release.

## Historical context

`HANDOVER/`, `PORTFROMFABLETASKLIST.md`, `IMPROVEMENT-PLAN.md`, dated audit reports,
and `docs/simplification/` record earlier decisions. Read them with the current
root guide. `INCONSISTENCIES.md` and `DEV-ISSUES.md` are superseded.

[Inbox network isolation](../deploy/INBOX-NETWORK-ISOLATION.md) records the retired
Inbox1 installation. Its old service, port and installer are not the active
Inbox recovery procedure. Keep the old service masked. The optional disabled
bridge in demo code is separate from these retired production instructions.
