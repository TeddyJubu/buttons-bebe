# Dependency readiness review — 2026-09-07

Read-only production package metadata was compared with tracked requirements. No production installations or restarts were performed.

## Python runtime reproducibility

`tools/.venv` has 32 runtime distributions; `KB/.venv` has 57 (pip excluded). Both run MCP 1.26.0. Direct requirements already pin that SDK, but CI resolves its transitive dependencies afresh. This permits a successful gate against versions different from production. It is distinct from the separately installed Hermes MCP 2.0 compatibility problem; upgrading the service SDK blindly is not a remedy.

The adjacent runtime-constraints.txt files record every observed installed runtime version. The requirements.lock files were generated for Linux x86_64/Python 3.12 with hashes and those constraints. The resolved name/version sets exactly equal the observed installed sets: zero upgrades, additions, or omissions. Hash resolution is not a fresh-environment regression test.

Two overlapping packages differ between the existing environments: tools uses cffi 2.1.0 and charset-normalizer 3.4.9; KB uses cffi 2.0.0 and charset-normalizer 3.4.8. Keep separate environments when verifying exact runtime parity. Do not merge the constraints into one resolver invocation. uv reports KB's charset-normalizer 3.4.8 as yanked; the registry provided no explanation. Its presence is recorded honestly, not presented as a security clearance. A follow-up 3.4.9 compatibility review can be made separately.

Approved source adoption: CI installs each lock with `--require-hashes` into separate environments, verifies exact installed version receipts, runs actual MCP catalog/schema tests under both, and runs KB tests under the KB lock. KB setup consumes its hashlock; keep existing live environments untouched until those gates pass and a separately reviewed runtime rebuild is needed. The deploy receiver already classifies requirements.lock as dependency material, so changes should not silently reinstall live dependencies.

## WhatsApp lock advisory metadata

`npm audit --package-lock-only --ignore-scripts --json` reports zero high/critical and three moderate affected package paths (qs, body-parser, express), deriving from two qs advisories. This is registry metadata, not evidence of live exploitability or installed-node_modules parity.

The lock contains qs 6.15.3, Express 4.22.2, body-parser 1.20.6, and Baileys 6.7.23. Both Express and body-parser constrain qs to ~6.15.1, excluding the fixed 6.16.0. At review time the registry listed no newer Express 4.x or body-parser 1.x release.

- Maintainer advisory [GHSA-x5fp-wj9c-mxmx](https://github.com/ljharb/qs/security/advisories/GHSA-x5fp-wj9c-mxmx): bracket-key comma parsing can bypass configured array limits; fixed in 6.16.0. Exploit requires `comma: true`; no such configuration was found in this app.
- Maintainer advisory [GHSA-4mjr-xmp4-gh2g](https://github.com/ljharb/qs/security/advisories/GHSA-4mjr-xmp4-gh2g): hostile constructor.isBuffer can throw during stringify after certain parse configurations; fixed in 6.16.0. No application qs stringify sink was found. Express query parsing alone does not establish this exploit chain.

Recommended bounded follow-up: a scoped qs 6.16.0 override, regenerated lock, clean isolated npm ci and current HTTP/security tests plus advisory regression cases. Do not use broad npm audit fix or change Baileys major versions; pairing/auth behavior needs separate review. The approved follow-up adds only the qs 6.16.0 override: the regenerated lock changes only that package. Clean isolated npm ci with scripts disabled and all 10 WhatsApp tests passed, including the two advisory regression cases and normal query compatibility. The post-change metadata audit reports zero advisories. Production node_modules remain untouched; root must prepare and review a candidate before switching.

## Deploying whatsapp-connect dependency changes (owner runbook)

CD never installs or mutates live dependencies
(`deploy/cd/source_release.py` classifies `package.json`/`package-lock.json` as
dependency material and refuses deploys that change them), so after any
whatsapp-connect lock change is merged, root applies it manually on the VPS.
Because CD refuses those deploys, **the live checkout at
`/root/Buttonsbebe Agent` never receives the merged lock — fetch the reviewed
manifests from the repo before installing anything.**

The reviewed tool `tools/ops/whatsapp_dependency_switch.py` performs this
switch atomically under a deployment lock: it validates fingerprints against a
root-approved plan, stops the service before touching `node_modules`,
preserves the auth store, journals a rollback receipt, and restarts only after
the candidate is verified. Its candidate path is fixed:
`/opt/buttonsbebe/whatsapp-candidate-18ca775` (see
`deploy/WHATSAPP-DEPENDENCY-SWITCH.md` for the full safety contract). `npm ci`
inside the live directory bypasses all of that (mutating `node_modules` under
the running service, restarting before verifying, no rollback) — do not run
it for a dependency change.

```bash
# 1. Stage the candidate at the tool's fixed path, built from the REVIEWED
#    commit (never the stale live checkout): fresh clone at the merged sha,
#    isolated npm ci, then the tests.
git clone <repo> /opt/buttonsbebe/whatsapp-candidate-18ca775
cd /opt/buttonsbebe/whatsapp-candidate-18ca775 && git checkout <merged-sha>
npm ci --ignore-scripts               # installs exactly the reviewed lock
node --test                            # 15 tests incl. the two qs regression cases

# 2. Inventory, review, approve — run from the repo checkout, absolute tool path:
umask 077
python3 /root/Buttonsbebe\ Agent/tools/ops/whatsapp_dependency_switch.py \
    --inventory > /root/wa-switch-approved.json
#    Review the printed live/candidate hashes and patched-server sha, then apply:
python3 /root/Buttonsbebe\ Agent/tools/ops/whatsapp_dependency_switch.py \
    --approved-plan /root/wa-switch-approved.json --apply

# 3. Prove live parity before calling it done.
curl -s http://127.0.0.1:8085/wa/status   # "qs" must echo the locked version (6.16.0)
```

The `qs` field in `/wa/status` is the live-parity proof: it reports the
version actually loaded, closing the gap where CI-green tests describe a
lockfile the VPS never installed. Verify it before considering the deploy
done. If the switch tool fails at any phase, it rolls back to the original
dependency set itself (see the recovery id in its output); do not hand-edit
live `node_modules` to recover.
