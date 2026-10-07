# ADR-015: Authenticate Hermes output before review

- Status: Accepted
- Date: 2026-08-23
- Scope: processor/hermes_runner/, processor/hermes_runner.py, and
  processor/draft_cleaner.py

## 1. Context

Hermes reads customer-controlled content through read-only MCP tools and
returns a draft for a human review console. The former flat runner tried to
identify the model's DRAFT and JSON_RESULT blocks by position and then by
customer-text echo. Neither is authentication: tool output can print a
customer-planted block before the model, an agent note can appear after it,
and a real model answer can quote KB or customer language.

Priority, owner notification, dashboard reason text, and the customer draft
all come from this output. A forged or malformed result must not clear an
alert, claim a Gorgias write, or become sendable. T-FIX-4 split the runner into
prompt, extraction, constants, and subprocess modules while retaining the flat
caller surface.

## 2. Decision

### 2.1 Exact per-run token

After the customer-message should_draft gate and immediately before invoking
Hermes, the runner mints an 8-byte operating-system CSPRNG token. It is not
shown to the customer or persisted. Both the draft tags and JSON verdict must
carry that exact token:

    <DRAFT:<run-token>> ... </DRAFT:<run-token>>
    JSON_RESULT[<run-token>]: { ... }

Only exact-token markers are trusted. Empty, wrong, untagged, malformed,
overflowing, or otherwise unauthenticated output returns a distinct high,
sensitive, owner-notifying result with an empty draft and no_draft=true. There
is no degraded untagged parser.

### 2.2 Conservative bounded parsing

Verdict extraction accepts at most 50 tokenized candidates and scans each
balanced JSON object within an 8,000-character bound. It requires priority,
string reason, action, and notify_owner; validates priority; normalizes
booleans; drops arbitrary keys; maps an unknown action to sensitive_draft; and
forces gorgias_priority_set=false and note_posted=false.

Multiple authenticated verdicts merge conservatively: priority takes the
maximum severity, notify_owner is true if any candidate requests it, and
action takes the most severe value. Unknown actions rank above known actions.
The merged reason is a generic conflict warning rather than untrusted
model-authored text.

Draft extraction requires complete matching exact-token tags. Empty bodies,
nested or mismatched markers, more than 50 markers, and multiple surviving
drafts fail closed. A legitimate short tagged reply remains valid; length is
not an authentication signal.

### 2.3 Distinct failure paths

The sendable fallback is reserved for runner execution failures such as a
missing binary, non-zero exit, timeout, or an unexpected local exception.
Known output-authentication anomalies are handled explicitly and receive the
non-sendable token-failure result. draft_for_console honors no_draft and never
manufactures a fallback for an unauthenticated or self-commentary-only result.

### 2.4 Input boundary, read-only tools, and last-mile cleaning

Customer text and store name are neutralized at the prompt boundary for
JSON_RESULT, draft tags, and agent-note markers. The token still protects text
that arrives later through tool output. The command builder uses the explicit
read-only toolset allow-list, and model output cannot turn Gorgias side-effect
flags on.

The should_draft gate remains first. Subject and body are judged independently,
and over-limit input drafts rather than being truncated into silence.

After authentication, draft_cleaner may only shorten or suppress: it removes
line-anchored model self-talk and repeated bodies while preserving normal
replies. Removed tails are labelled as unverified model text, sanitized, and
bounded before entering the reviewer and owner-visible reason. If cleaning
removes everything, the result is elevated, owner-notifying, and non-sendable.

Self-talk patterns remain line-anchored and share one non-overlapping prefix;
the earlier overlapping whitespace/dash prefixes were superlinear on long
horizontal rules. Internal reviewer notes are cut from the sendable body but
returned without keyword filtering, because a keyword veto both retained
ordinary self-talk and discarded legitimate stock, identity, or billing warnings.
Repeated-body cleanup uses a minimum meaningful unit and tests every possible
copy count, preserving the first copy's original formatting.

The acknowledgement gate is a bounded linear token scan, not the former
whole-message alternation whose overlapping phrases caused exponential
backtracking. It suppresses only when every token is allow-listed and at least
one genuine acknowledgement anchor is present. Questions, decision words,
unknown words or symbols, sad/angry emoji, and non-Latin content all draft.
Subject noise uses a single whitespace class around order identifiers; two
adjacent optional whitespace groups were quadratic on padded subjects.

Subject and body are judged independently. Whitespace is collapsed before the
length check, and an input still over its bound always drafts. Truncating a long
acknowledgement could otherwise hide a complaint or question at the tail and
silence the ticket.

The flat processor/hermes_runner.py shim continued to export
build_hermes_command, draft_for_console, and process_ticket_with_hermes.
(Update 2026-10-07: the package always shadowed the shim, so it was removed.)

## 3. Invariants and threat model

- No draft or verdict is trusted without the exact token minted for that run.
- Authentication failure is high, sensitive, owner-notifying, and non-sendable.
- Runner execution failure retains the existing reviewable fallback.
- Multiple trusted candidates can only raise caution; they cannot clear an
  alert or lower priority or action.
- Model output cannot claim Gorgias priority changes or internal notes.
- Only the processor sets no_draft; the model cannot suppress its own draft.
- A human remains the only sender from the review console.

The attacker controls subjects, bodies, quoted history, and any read-only tool
text that echoes them. The model may emit malformed JSON, repeated markers,
invalid actions, long reasons, self-commentary, or contradictory candidates.
Marker counts, JSON scans, reason and note lengths, and draft shapes are
bounded. The failure mode is visible manual handling, not a sendable guess.

## 4. Rejected alternatives

- First or last marker selection: either position can contain attacker text.
- Echo detection as proof: it misses subject and thread content and can reject
  a genuine KB-template answer.
- An untagged degraded parser: it reintroduces attribution guessing.
- Discarding every short draft: authenticated short replies are legitimate.
- Copying the whole parsed object: arbitrary keys could forge console metadata.
- Using the normal fallback for auth errors: it would create a sendable-looking
  answer while ownership is unknown.

## 5. Testing evidence

- processor/test_hermes_extract.py covers exact-token extraction, wrong and
  untagged markers, malformed and overflow cases, cautious merge behavior,
  boolean normalization, invalid actions, and arbitrary-key dropping.
- processor/test_hermes_readonly_prompt.py, processor/test_hermes_toolset.py,
  processor/test_loop_responsive.py, and processor/test_draft_cleaner_wiring.py
  cover marker neutralization, read-only command construction, the
  pre-subprocess gate, timeout behavior, and cleaner wiring.
- Demo adversarial and security tests exercise hostile marker payloads and
  enforce Cute Things isolation. The pure extraction contract does not require
  a live client or VPS model run.

## 6. Rollback

Revert the T-FIX-4 Hermes refactor as one reviewed change. The prior flat
runner is preserved at the parent of T-FIX-4 commit `303b2a8`; this split
changes no database or external-service state. Run the offline release gate
and the demo isolation checks before deployment.
