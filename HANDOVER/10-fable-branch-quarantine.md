# Fable branch quarantine

**Decision:** `Fable_buttonsbebe` is quarantined as a local prototype. It is not
part of the live `main` Hermes system, and no Fable service, timer, reverse proxy,
or deployment unit may be installed from this branch without a separate approval.

## Evidence from the bounded audit

- The branch is separate from `main` and contains the prototype under `fable/`.
  The live `main` checkout has no Fable runtime entry point or systemd unit.
- The branch has no `fable/requirements.txt`, `pyproject.toml`, or equivalent
  runtime manifest. Its `fable/scripts/test.sh` installs pytest, coverage, and
  httpx into whichever interpreter is selected, which is not a reproducible or
  offline-safe release gate.
- An extracted-branch run without dependency injection stopped after **330
  passing tests** because `kb/scripts/sync_products.py` could not import the
  undeclared `requests` package.
- With a no-network `requests` stub, the same bounded unit/integration command
  completed **351 tests**, showing the failure is currently manifest hygiene;
  this does not make the branch merge-safe.
- Default configuration points at loopback emulators and uses the mock brain,
  but environment variables can change `FABLE_HOST`, Shopify/Redo/mailbox bases,
  the brain, and email transport. The Gorgias-compat routes explicitly accept
  Basic auth but ignore it. Binding beyond loopback or selecting real transports
  therefore needs a new authentication and egress review.

## Merge gate before this can leave quarantine

1. Add a pinned Fable runtime/test dependency manifest, including the shared KB
   sync dependency currently missing (`requests`), and make the test command
   fail rather than installing packages implicitly.
2. Add a safe-host/egress policy: keep local defaults, reject non-loopback
   service bases in local mode, and require explicit authenticated production
   configuration for any real transport or external brain.
3. Replace ignored Basic auth with real authentication or keep the compat API
   loopback-only; add tests proving the chosen boundary.
4. Run the full suite in a clean environment without injected module stubs,
   then run a bounded local demo and stop every process it starts.
5. Obtain a fresh Terra review and an explicit owner decision before any merge,
   deployment, or connection to Gorgias/Shopify/Anthropic/SMTP/IMAP.

Until all five gates pass, treat Fable as reference/prototype code only. The
current live architecture remains the Baileys WhatsApp bridge, webhook,
processor, and KB services documented elsewhere in `HANDOVER/`.
