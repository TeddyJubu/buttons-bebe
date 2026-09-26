# AI draft reliability

This behavior supersedes earlier instructions to always draft, convert all
knowledge gaps to HIGH, or substitute a polite acknowledgment after failure.
It is implemented on an isolated branch; deployment and KB publication are
separate operations.

## Result contract

`ready` is a usable suggestion, `needs_review` means staff must complete the
answer, `no_reply` suppresses acknowledgments, and `failed` displays **AI draft
unavailable**. `retry_wait` carries a durable next retry timestamp. Obsolete
attempts end as `superseded` and cannot publish. Staff tasks/missing facts live
in authenticated metadata outside customer prose. Manual composition, explicit
Use draft, local AI edits, dismissal and confirmed Send remain separate.

Only `timeout`, `process_exit`, and `runtime_error` are transient. A cycle has
one initial attempt and two retries after 30 and 120 seconds. Interrupted
attempts consume the same budget. The synchronous process helper kills/reaps
the whole process group; the existing singleton lock remains the execution
boundary. No background generation thread is introduced. Set HERMES_TIMEOUT=240
and PROCESSOR_JOB_TIMEOUT=270; the outer budget must exceed the subprocess one.

Queue completion is not generation success. Separate attempt counts record
timestamps, elapsed duration, sanitized error category, outcome and the prior
failed candidate. A retry never replaces a successful candidate. Publication
and retry reservation recheck the newest customer message, exact draft hash
and human-action ledger under the same SQLite writer lock as Send reservations.
Pending/uncertain human actions win. A duplicate operation ID returns the same
queued operation; reuse with different parameters is a conflict.

Authenticated POST `/console/api/ticket/{id}/retry-draft` takes operation_id
(UUID), source_message_id, and draft_revision (SHA-256). It queues the existing
job, returns 202, and requires no Inbox write grant. The underlying private
route remains `/dashboard/api/ticket/{id}/retry-draft`. It never invokes Send.

## Additive migration

Normal webhook/processor initialization adds generation metadata to
ticket_results, next_attempt_at/generation_cycle_attempts/expected revision to
job_queue, and creates draft_generation_attempts and draft_retry_requests.
No historical jobs are replayed. Legacy records remain readable; only recorded
failure reasons identify old placeholder replies as unavailable. The projection
also handles a database without the new columns. Back up SQLite using its backup
API before release; do not copy a live WAL database file alone. Source rollback
does not undo additive columns, attempt history, or customer actions.

## Reply rules

Identify the newest request before inherited subjects/intents. Preserve
unresolved sensitive cases and prior alert records when a thanks arrives.
Use details already supplied; ask at most one necessary clarification. Store-wide
shipping/hours questions do not require an order number. A broken return portal
needs a specific staff task, not the same broken link again. Product measurements
need evidence for that product; ordinary gaps remain normal priority.

A necessary customer clarification remains usable when authenticated metadata
explicitly sets review_required=false, including a no_kb_match result. Older
results without that metadata retain staff review; multiple verdicts retain any
review requirement. A future staff task does not block asking the customer for
the product or order identifier needed first.

Order pickup requires confirmed readiness, independently of pickup selection,
outdoor-bin accessibility and staffed hours. Regular hours do not prove holiday
or particular-day opening. Unfulfilled is not evidence of packing, dispatch,
prioritization, cancellation or a refund. A response requiring staff-only facts
stays held until the operator writes a completed response.

## Notification checks and release

GET `<protected send URL>/check` authenticates with the existing send credential
and returns route availability, bridge connection, and destination configuration.
It never sends. Processor diagnostics are stored without the secret URL and
shown through authenticated console reads and readiness diagnostics. Bridge
acceptance is not device delivery. Uncertain alerts remain in the attention
queue; neither this migration nor retry automatically resends historical alerts.
Actual delivery confirmation uses the existing human-operated test-alert control.

Merge to main auto-deploys only after CI. Before merging, follow deploy/cd/README.md:
back up state; review runtime configuration changes; update only applicable
approved fingerprints; verify dependency manifests and route readiness. Preserve
the existing bridge credentials, owner destination, authentication and public
routes. The protected processor send URL must match the running bridge token
route; never commit the real URL or token. The new check route becomes available
only after bridge code deployment. Do not call `/wa/test` as an automated check.

KB files in this PR require the documented KB-admin publication/index workflow
after review. Code deployment neither publishes those files nor rebuilds the
index. Validate the new shipping FAQ and intent templates after promotion.
The tracked Hermes SOUL/skill guidance also requires the documented profile
installation workflow; it is not part of the automatic source inventory. Do not
leave the old always-draft/knowledge-gap escalation instructions in the active
profile when publishing this behavior. The runtime prompt remains authoritative
for its exact tokenized output contract.

Before release require the offline gate, desktop/mobile Inbox checks, and all
48 isolated production-model scenarios with zero timeouts and no safety,
grounding or output-integrity failures. Record case-specific defects and reruns;
capturing a model response alone is not a pass. See testing/HOW-TO-RUN.md.

At release record the maximum draft_generation_attempts.id. Review the next 100 terminal results
after that marker: generation success (target >=95%), answer usefulness,
incorrect escalation, and bridge acceptance. Assess held staff-input drafts
separately from usable ready replies and acknowledgments separately from model
generation. Report each substantive defect with its ticket/result ID and remedy;
do not conceal defects in an average score. This audit depends on a separate
production release and subsequent traffic.

Use `tools/audit_reply_quality.py --db <runtime SQLite> --after-attempt-id <release marker>
--output <new private JSON>` to collect the review packet without writing the
runtime database. It excludes intermediate retry_wait outcomes and reports
acknowledgments separately. A packet with fewer than 100 rows is incomplete.
The tool calculates generation success only; each record's usefulness,
escalation judgment and substantive defect list still require review.

An abandoned attempt retains the newest request's deterministic business urgency
in its journal. Recovering an unavailable draft does not downgrade a dispute or
clear its alert ledger. In-process recovery attempts an urgent owner alert once;
restart recovery retains the urgent result in the attention queue while retries
remain scheduled.

Proposed KB articles can be tested with the hash-pinned policy snapshot described
in testing/HOW-TO-RUN.md. This uses published search ranking with proposed article
content, without publishing articles or rebuilding the live index. Verify index
retrieval separately after the approved KB publication.

When only parsing/cleaning changes after a model capture, qa_postprocess.py
--reparse revalidates the immutable authenticated stdout through the current
runner, cleaner and orchestrator without new model calls or external effects.
It records each input hash and executed source hashes. Report this separately
from a fresh model run, retain the original failures, and rerun the model when
the prompt or knowledge changes.
