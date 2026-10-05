# D7 owner review

D7 combines automated source/import verification with the owner's acceptance of
representative conversations and workflows. Passing tests, generating this packet
or an instruction to perform D7 does not itself record acceptance.

**Current decision, 2026-09-28: accepted for the offline sample with the listed
gaps.** The owner explicitly stated: "Accept D7 for the offline sample with the
listed gaps". The private `owner-decision.json` and `OWNER-DECISION.md` beside the
v2 packet record that decision and its scope. D7 is complete; the original packet
retains its historical pending status unchanged. Live activation is not authorized.

The current private packet is in
`intake/.local/gorgias-owner-review-20260928-v2/`. Open `START-HERE.md`, then
`owner-review.md` in Codex. The HTML alternative provides expandable side-by-side
comparisons in a browser on the workspace machine. The Codex desktop browser
cannot directly open this remote workspace's file URL; no public hosting or
production route has been added to work around that restriction.

## Review order

| Case | Why selected | Saved messages |
| --- | --- | --- |
| 1 | Longest saved conversation and attachment metadata | 24 |
| 2 | Open ticket with usable reply evidence | 1 |
| 3 | Closed conversation with historical staff replies | 2 |
| 4 | Missing or unusable latest-message evidence | 1 |
| 5 | Self-addressed incoming message | 1 |
| 6–7 | Two separate tickets from the same customer | 1 each |

These are structural examples, not random or statistically representative
sampling. Review participants, status/assignment, message order, text, timestamps,
visibility, headers, attachment references and the derived reply eligibility.
Internal IDs differ from source IDs, timestamps use UTC and text is trimmed at
its edges. Urgent priority maps to critical. Raw original records remain retained.
Each message shows both original and imported values; customer HTML and Markdown
remain inert text.

Then review the six workflows in `START-HERE.md`: keep conversations separate;
reopen on a qualifying new customer reply; keep staff changes local; review and
confirm simulated replies; block uncertain delivery; recover into a new workspace.
These reflect the D3–D6 test results. Human acceptance remains a separate decision.

## Evidence and limits

- All 50 saved tickets / 211 messages passed 2,388 checks: the 2,088 source-field
  checks plus 300 display/contact/order/history checks. All 261 original source
  records are retained in the fresh review workspace. It contains no simulated
  deliveries or fabricated customer messages.
- The seven-case comparison contains 31 messages. The private browser check
  confirms expansion, responsive layout, no executable content, no external
  requests and no customer-data screenshots.
- This is a bounded export, not the whole account. The 22 real attachment
  references have no file bytes. There are no private source notes or spam/trash
  cases. Four messages lack usable headers; four conversations are withheld from
  reply simulation. The longest thread has 24 messages and no source ticket
  supplies `messages_count`. Broader and multi-page source coverage is unproven.
- File recovery, private notes, same-subject ambiguity, spam and automated-message
  edge cases also have synthetic coverage from earlier stages. That does not fill
  the real-data coverage gaps.
- Authentication, team permissions, AI/context integration, real channel adapters,
  outgoing attachments, CC/BCC, alternate recipients, offsite recovery and cutover
  are later work. No acceptance here enables real ingress, egress or deployment.

## Record the owner's decision

Ask the owner to inspect the comparison and reply in the chat with acceptance of
this offline sample and the listed gaps, or the case/message/workflow to change.
Keep D7 unchecked while the decision is pending. Do not count a tool-generated or
automated result as the owner's decision.

After an explicit decision, save its exact text, timestamp, packet digest and
scope in a new private decision record beside the packet. Keep the original
packet unchanged. Update `INTAKE-TASKLIST.md`; mark D7 complete only if the owner
accepts the reviewed sample and workflows with the unresolved gaps recorded.
If changes are requested, resolve them and present updated evidence first.

## Regenerate into a fresh workspace

```bash
python3 -m intake.owner_review intake/exports/gorgias-20260928T093215-921504Z --workspace new-owner-review --account buttonsbebe
```

The tool installs the offline network/process guard, verifies the saved manifest,
imports and reconciles into a new private workspace, checks repeat/rollback
behavior and display fields, then writes the packet with acceptance pending.
It refuses existing workspaces and never changes older proof workspaces or the
saved export. A failed check prevents a ready-to-review packet. The private JSON
records selected IDs and a packet digest; stdout contains counts and paths only.

Runtime files remain ignored by Git with 0700 directories and 0600 files. Keep
customer contents out of chat, logs, screenshots and published artifacts.
