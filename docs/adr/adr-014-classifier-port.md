# ADR-014: Deterministic classifier port invariants

- Status: Accepted
- Date: 2026-08-23
- Scope: processor/classifier/ and processor/classifier.py

## 1. Context

The deterministic classifier is a read-only first-pass safety screen. It
returns priority, sensitivity, draft, owner-notification, and audit-match
signals for the orchestrator. Its safety property is asymmetric: it may
escalate, but it must not lower urgency or suppress a draft.

Before T-FIX-3, the implementation was a 1,606-line module containing the
store's original rules, the Fable-port rules, contextual guards, structural
anger logic, and the history of fixes for false positives, quoted mail,
Unicode punctuation, and regex blow-ups. T-FIX-3 split those responsibilities
into a package while preserving the old import and script surfaces.

## 2. Decision

### 2.1 Contract and compatibility

The classifier remains deterministic, standard-library-only, and side-effect
free. Sensitive results always notify the owner; all results request a draft.
The classifier does not call external APIs or write to Gorgias.

The main tables are the compatibility baseline. Ported rules may add signals,
but cannot remove a main match. The package facade, compatibility module,
mutable flattened rule lists, and processor/classifier.py shim preserve legacy
imports, mutation tests, and the built-in self-test command.

### 2.2 Separate rule provenance and text views

Rules are loaded from checked-in JSON-compatible snapshots. Each Rule records
its pattern, a representative exemplar, the source view, and its tier.
RULE_TABLES is canonical; flattened lists are compatibility projections.

Main rules read the raw subject and body, with a folded-smart-quote copy only
as a supplementary view. They are not exposed to the port's length cap or
boilerplate filter. Ported rules read the bounded, folded, filtered view.
Filtering removes paragraphs that match the checked-in store-boilerplate
patterns. The 60,000-character head and tail are joined by a word-character
sentinel so truncation cannot manufacture a phrase or word boundary.

Quoted history and structural anger use the customer's message view rather
than machine-generated subjects or inherited store text. Audit excerpts
collapse attacker-chosen whitespace before reaching logs.

### 2.3 Contextual escalation and bounded work

Weak vocabulary such as missing, lost, stain, ripped, without, and different
requires order or delivery evidence. Browsing questions suppress weak matches
unless a problem shape proves damage or delivery failure. The unguarded weak
table remains empty. Trade and wholesale owner requests are distinguished from
manager demands. Caps-lock requires grievance evidence or complaint grammar;
positive language prevents structural anger from paging the owner unless a
hard grievance anchor is present.

Ticket text, subjects, quoted history, and pasted HTML are untrusted. Bounded
scan windows, linear whitespace forms, bounded quote patterns, and a safe
truncation seam prevent attacker-controlled input from stalling the
synchronous processor.

## 3. Invariants

- Main rules always see the main/raw view; a port filter or cap cannot
  de-escalate a main match.
- Sensitive means at least high priority and owner notification.
- A normal result still requests a draft.
- Matched phrases and context are log-safe and bounded.
- Rule tables and guards remain inspectable and mutation-testable.

## 4. Threat model and rejected alternatives

Inputs may contain store boilerplate, quoted history, smart punctuation,
long whitespace, long threads, or crafted regex stress shapes. The design
prefers a recoverable owner alert over silently losing a sensitive ticket, but
guards common product and care language to control alert fatigue.

Running every rule on one filtered string was rejected because it hid main's
complaints. An unguarded keyword expansion was rejected because ordinary
shopping and trade messages use the same words. Copying the retired Fable
deployment classifier was rejected because its contract and dependencies were
different. Keeping one large module was rejected because provenance and
compatibility were difficult to review.

## 5. Testing evidence

- classifier/selftest.json contains 106 labelled messages; classifier.selftest
  runs 213 checks, including sensitive-notify and chargeback invariants.
- processor/test_classifier_rules.py freezes the main tables and side
  channels, tests view ownership, benign and sensitive corpora, quotes,
  boilerplate, caps, mutation behavior, and hostile regex shapes.
- tools/compare_classifier.py --samples 10000 compared the pre-split and
  package implementations in isolated workers on priority, sensitivity, and
  owner-notification. **Retired 2026-09-17 (owner decision, simplification
  Wave 4, report 04-5):** the comparison proved the one-time T-FIX-3
  structural split — it stayed green across 231 deploys to main
  (2026-08-23 → 2026-09-17) — and re-proved nothing after that. The gate
  step, `processor/classifier_shim.py`, the harness, and CI's full-history
  fetch were deleted together; the selftest and test_classifier_rules.py are
  the ongoing net. The pre-split implementation remains retrievable from git
  history at the parent of `ba138d5`.

## 6. Rollback

Revert the T-FIX-3 package and parity changes as one reviewed change. The
previous flat classifier is preserved at the parent of T-FIX-3 commit
`ba138d5`; this refactor has no database, index, or external-service migration.
Run the offline release gate before deployment.
