"""Hermes headless runner — invokes Hermes in one-shot mode to process tickets.

Uses `hermes --yolo -z "prompt"` to run the full ticket processing
pipeline (read context, search KB, classify, and return a console draft).
Parses the JSON_RESULT block from stdout for the job processor.

Performance:
  - KB search + classify + draft: 6-10 seconds
  - Full read-only pipeline with Gorgias context: 30-60 seconds
  - Timeout: 120 seconds (configurable in settings)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from typing import Any

from config import get_settings
from logging_setup import get_logger, log_event

logger = get_logger(__name__)

# Regex to find the JSON_RESULT marker — the actual JSON is extracted
# by _extract_json_block which handles balanced braces.
_JSON_RESULT_MARKER_RE = re.compile(
    r'JSON_RESULT:\s*(\{)',
    re.IGNORECASE,
)


def _extract_json_block(text: str, start_pos: int) -> str | None:
    """Extract a balanced JSON object starting at start_pos (the opening {).

    Handles nested braces correctly by counting open/close braces.
    Returns the raw JSON string, or None if unbalanced.
    """
    depth = 0
    in_string = False
    escape = False
    for i in range(start_pos, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start_pos:i + 1]
    return None  # unbalanced


# Regex to extract draft text from <DRAFT>...</DRAFT> tags
_DRAFT_TAG_RE = re.compile(
    r'<DRAFT>\s*(.*?)\s*</DRAFT>',
    re.DOTALL | re.IGNORECASE,
)


def _extract_draft(output: str) -> str | None:
    """Extract the draft text from <DRAFT>...</DRAFT> tags in Hermes output."""
    match = _DRAFT_TAG_RE.search(output)
    if match:
        return match.group(1).strip()
    return None


# Default result if Hermes fails or output is unparseable
_FALLBACK_RESULT: dict[str, Any] = {
    "priority": "high",
    "reason": "Hermes invocation failed — defaulting to high for safety",
    "action": "sensitive_draft",
    "notify_owner": True,
    "gorgias_priority_set": False,
    "note_posted": False,
}


def draft_for_console(hermes_result: dict[str, Any]) -> str:
    """Return only a real Hermes draft; never echo customer input as a reply."""
    return str(hermes_result.get("draft_text") or "").strip()


def _build_prompt(ticket_id: int, message_text: str, ticket_subject: str,
                  customer_email: str, intents: list) -> str:
    """Build the one-shot prompt for Hermes.

    Truncates very long messages to avoid prompt overflow, and flags
    empty messages for safe handling.

    Hermes is always read-only. The draft is captured from stdout and shown in
    the console; only a human-triggered console endpoint may send or post it.
    """
    intents_str = ", ".join(intents) if intents else "none"

    # Truncate very long messages (keep first 3000 chars — enough for
    # the customer's actual message even with some thread noise)
    if len(message_text) > 3000:
        message_text = message_text[:3000] + "\n[... truncated for length ...]"
        truncated_note = " (truncated — very long message)"
    else:
        truncated_note = ""

    if not message_text or not message_text.strip():
        message_text = "[EMPTY MESSAGE — no customer text in body. " \
                       "Check if this is a survey, thank-you, or system email.]"

    write_steps = (
        f"7. Stay READ-ONLY: do NOT use curl to PUT or POST, do NOT set Gorgias "
        f"priority or tags, and do NOT post an internal note or customer reply.\n"
        f"8. ALWAYS draft a reply based on KB content + returns + order data, "
        f"including for sensitive topics (see drafting rules below).\n"
        f"9. Output the FULL DRAFT TEXT between <DRAFT> and </DRAFT> tags for the "
        f"console's human review workflow.\n"
        f"10. Output the JSON_RESULT line at the very end with "
        f"note_posted=false and gorgias_priority_set=false.\n\n"
    )
    draft_output = (
        f"\nAfter your analysis, output the complete draft between these tags:\n"
        f"<DRAFT>\n"
        f"...your full draft here...\n"
        f"</DRAFT>\n\n"
        f"The console will show this text to a human, who may edit it and choose "
        f"Send reply, Draft as internal note, or Request edit. Hermes does not "
        f"perform any of those Gorgias writes.\n\n"
    )
    safety_writes = (
        f"- DO NOT use curl for ANY Gorgias writes. Do NOT set priority or tags. "
        f"Do NOT post a note or reply. All external tools are read-only.\n"
    )

    return (
        f"Process Buttons Bebe support ticket {ticket_id} autonomously.\n\n"
        f"Ticket context from webhook:\n"
        f"- Ticket ID: {ticket_id}\n"
        f"- Subject: {ticket_subject}\n"
        f"- Customer email: {customer_email}\n"
        f"- Customer message (RAW — may contain email thread noise, "
        f"spelling errors, quoted replies):\n"
        f"{message_text}\n\n"
        f"- Gorgias intents: {intents_str}\n\n"
        f"You have three MCP servers connected as tools:\n"
        f"1. buttonsbebe_gorgias: get_ticket, get_ticket_messages, "
        f"get_customer, search_customer (read-only)\n"
        f"2. buttonsbebe_kb: search_kb — searches policies, FAQs, "
        f"the current active product catalog, 22 intents, exemplar tickets\n"
        f"3. buttonsbebe_redo: get_order, get_returns_for_order, "
        f"get_return, list_recent_returns — order shipping/tracking + returns/RMA\n\n"
        f"Follow the ticket-processor skill workflow:\n"
        f"1. Read the ticket: call get_ticket(ticket_id={ticket_id}) "
        f"via the gorgias MCP tool\n"
        f"2. NORMALIZE the message before KB search:\n"
        f"   a. Strip quoted email replies, order confirmations, URLs, "
        f"HTML, signatures\n"
        f"   b. Keep ONLY the customer's actual words\n"
        f"   c. Fix spelling mistakes (thist→this, recieved→received, etc.)\n"
        f"   d. Rewrite vague phrasing into clear search terms\n"
        f"   e. If message is empty after cleaning → draft generic acknowledgment, do not guess\n"
        f"   f. If 3+ customer messages with no agent reply → CRITICAL\n"
        f"3. Search the KB: call search_kb with the CLEANED query "
        f"(not the raw message)\n"
        f"   - Try cleaned message → then broader keywords → then intent name\n"
        f"   - KB has products, policies, FAQs, intents — all searchable\n"
        f"4. Check returns if relevant: if customer mentions return/refund/"
        f"exchange/damaged/wrong item and you have an order number,\n"
        f"   call get_returns_for_order(order_name='<order_number>') "
        f"via the redo MCP tool\n"
        f"5. Check order & shipping if ticket mentions an order number: "
        f"call get_order(order_name='<order_number>') via the redo MCP tool\n"
        f"   - Returns shipping address, tracking number, carrier, "
        f"fulfillment status, delivery status, line items\n"
        f"   - Use to answer 'where is my order?', verify if shipped, "
        f"check address for changes\n"
        f"6. Classify priority as CRITICAL, HIGH, NORMAL, or LOW\n"
        f"{write_steps}"
        f"Priority definitions:\n"
        f"- CRITICAL: address change before shipment, wrong size before shipped, "
        f"pre-shipment cancellation, urgent delivery, fraud, angry/abusive, "
        f"repeated follow-ups (3+ msgs no reply). Gorgias: 'urgent'. Notify owner.\n"
        f"- HIGH: refund/chargeback post-fulfillment, damaged/wrong/missing item, "
        f"payment dispute, order not received. Gorgias: 'high'. Notify owner.\n"
        f"- NORMAL: order status, shipping delay, product/sizing question. "
        f"Gorgias: 'normal'. Do not notify owner.\n"
        f"- LOW: policy FAQ, thank you, general inquiry, newsletter, survey. "
        f"Gorgias: 'low'. Do not notify owner.\n\n"
        f"{draft_output}"
        f"Drafting style rules — FOLLOW THESE STRICTLY:\n"
        f"- DRAFTS MUST BE SHORT. Maximum 4 sentences for normal tickets, 5 for "
        f"sensitive. Do NOT write multi-paragraph drafts.\n"
        f"- Tone: warm, professional, direct. Like a real support agent typing a "
        f"quick reply — not an essay, not a report, not an explanation.\n"
        f"- Get to the point immediately. Answer the customer's question in the "
        f"first sentence. Don't preamble with 'Thank you for reaching out' unless "
        f"the ticket genuinely needs it.\n"
        f"- Match the KB intent templates in length and style. They are 2-4 "
        f"sentences. Your draft should be similar — not 5x longer.\n"
        f"- Do NOT include agent notes, analysis, or meta-commentary in the draft. "
        f"The draft is ONLY what the human will send to the customer. If you want "
        f"to note something for the human reviewer, put it AFTER the JSON_RESULT "
        f"line prefixed with 'AGENT NOTE:'.\n"
        f"- Do NOT explain why you're doing something — just do it. Instead of "
        f"'We're looking into the availability and will follow up with an update', "
        f"write 'We're checking on that for you and will follow up shortly.'\n"
        f"- Do NOT repeat information the customer already knows. If they asked "
        f"about order 12345678, don't restate 'regarding your order 12345678'.\n"
        f"- Do NOT add filler closings like 'Let me know if there's anything else I "
        f"can help with' or 'Thanks for shopping with Buttons Bebe' unless it "
        f"fits naturally in 1 short sentence.\n"
        f"- For 'thank you' / 'got it' messages: 1 sentence max. Example: 'You're "
        f"welcome! Let us know if you need anything else.'\n"
        f"- For no-action tickets (newsletters, test emails): output 'No reply "
        f"needed — [brief reason]' as the draft, nothing else.\n"
        f"\n"
        f"Drafting rules for sensitive topics:\n"
        f"- Sensitive topics (refunds, chargebacks, disputes, damaged/wrong/missing "
        f"items, lost packages, angry customers) MUST still get a draft — the human "
        f"reviews it before sending.\n"
        f"- For sensitive topics, prefix the draft with this header line:\n"
        f"  [SENSITIVE — REVIEW CAREFULLY BEFORE SENDING]\n"
        f"- Use the KB intent template language directly. Intent templates already "
        f"have approved short responses — use them as the basis, do not expand them.\n"
        f"- FORBIDDEN words in any sensitive draft: 'refund', 'money back', "
        f"'compensate', 'reimburse', 'credit your account', 'issue a refund', "
        f"'we will refund'. Use instead: 'we'll make it right', 'we're reviewing', "
        f"'we'll get back to you', 'we're looking into this'.\n"
        f"- For damaged/wrong items: apologize briefly, ask for photo with tag. "
        f"Example: 'Hi [name], so sorry about that! Could you send us a photo of "
        f"the item with the tag so we can get it sorted out for you?'\n"
        f"- For refunds/chargebacks: say it's being reviewed. Example: 'Hi [name], "
        f"we're reviewing this for you and will get back to you shortly.'\n"
        f"- If no KB match: 'Hi [name], thanks for reaching out. We're reviewing "
        f"your message and will get back to you shortly.' Tag as [SENSITIVE].\n\n"
        f"Safety rules:\n"
        f"- NEVER send an external reply or post an internal note. Return the draft "
        f"to the console for a human decision.\n"
        f"- Search KB with CLEANED query. Do not search with raw email thread text.\n"
        f"- Do not invent policy. If KB has no match, use generic acknowledgment (above).\n"
        f"- If KB marks topic as sensitive, ALWAYS draft with [SENSITIVE] tag + safe "
        f"acknowledgment language. Never skip drafting. The human is the safety gate.\n"
        f"- If message is empty/survey/thank-you with no question, classify LOW.\n"
        f"- Use MCP tools for reading (get_ticket, search_kb, get_order, get_returns_for_order).\n"
        f"{safety_writes}"
        f"- Product info is in the KB — search_kb finds sizes, prices, availability.\n\n"
        f"MCP tool selection by query type:\n"
        f"- Shipping/tracking: Redo get_order → KB search_kb\n"
        f"- Address change: Redo get_order (check shipped) → KB search_kb\n"
        f"- Return/exchange: Redo get_returns_for_order + get_order → KB search_kb\n"
        f"- Wrong/damaged item: Gorgias get_ticket → Redo get_order + get_returns_for_order → KB (SENSITIVE)\n"
        f"- Refund/chargeback: Redo get_returns_for_order → Gorgias get_customer → KB (SENSITIVE)\n"
        f"- Cancel order: Redo get_order (check shipped) → KB search_kb\n"
        f"- Order change/size: Redo get_order (check shipped) → KB search_kb\n"
        f"- Lost/not received: Redo get_order (tracking) → KB (SENSITIVE)\n"
        f"- Product/sizing: KB search_kb (active product catalog) → KB sizing guide\n"
        f"- Policy/FAQ: KB search_kb only\n"
        f"- Urgent/rush: Redo get_order (shipped?) → KB search_kb (CRITICAL)\n"
        f"- Customer history: Gorgias get_customer (Shopify orders)\n"
        f"- Thank you/survey: Gorgias get_ticket_messages → classify LOW\n\n"
        f"At the very end, output exactly this line:\n"
        f'JSON_RESULT: {{"priority": "<critical|high|normal|low>", '
        f'"reason": "<one sentence>", '
        f'"action": "<drafted|sensitive_draft|no_kb_match>", '
        f'"notify_owner": <true|false>, '
        f'"gorgias_priority_set": <true|false>, '
        f'"note_posted": <true|false>}}\n\n'
        f"IMPORTANT: priority and notify_owner must reflect the TICKET CONTENT. "
        f"A sensitive refund ticket is HIGH with notify_owner=true. Always return "
        f"gorgias_priority_set=false and note_posted=false because Hermes never "
        f"writes to Gorgias; those false values do not reduce urgency.\n\n"
        f"Be concise. Do not ask questions. Make your best judgment. "
        f"REMEMBER: The draft between <DRAFT></DRAFT> is what the customer "
        f"will see — keep it SHORT, WARM, and ON-POINT. No more than 4-5 "
        f"sentences. Do not include analysis or notes in the draft itself."
    )


def _parse_json_result(output: str) -> dict[str, Any]:
    """Extract the JSON_RESULT block from Hermes output.

    Returns a parsed dict, or the fallback result if not found.
    """
    match = _JSON_RESULT_MARKER_RE.search(output)
    if not match:
        log_event(logger, "WARNING", "No JSON_RESULT found in Hermes output")
        return dict(_FALLBACK_RESULT)

    # Extract the balanced JSON block starting at the opening brace
    raw_json = _extract_json_block(output, match.start(1))
    if not raw_json:
        log_event(logger, "WARNING", "JSON_RESULT block is unbalanced")
        return dict(_FALLBACK_RESULT)

    try:
        result = json.loads(raw_json)

        # Validate required fields
        required = {"priority", "reason", "action", "notify_owner"}
        if not required.issubset(result.keys()):
            log_event(logger, "WARNING", "JSON_RESULT missing required fields",
                      found=list(result.keys()))
            return dict(_FALLBACK_RESULT)

        # Normalize priority
        priority = str(result["priority"]).lower().strip()
        if priority not in ("critical", "high", "normal", "low"):
            log_event(logger, "WARNING", "Invalid priority in JSON_RESULT",
                      priority=priority)
            return dict(_FALLBACK_RESULT)

        result["priority"] = priority
        # Hermes has read-only tools. These fields describe real side effects,
        # so model output must never be allowed to claim that a write occurred.
        result["gorgias_priority_set"] = False
        result["note_posted"] = False

        return result

    except json.JSONDecodeError as exc:
        log_event(logger, "ERROR", f"Failed to parse JSON_RESULT: {exc}")
        return dict(_FALLBACK_RESULT)


def process_ticket_with_hermes(
    ticket_id: int,
    message_text: str,
    ticket_subject: str,
    customer_email: str,
    intents: list,
) -> dict[str, Any]:
    """Invoke Hermes headlessly to process a ticket.

    Args:
        ticket_id: Gorgias ticket ID
        message_text: Customer's message text
        ticket_subject: Ticket subject line
        customer_email: Customer's email address
        intents: List of Gorgias intent name strings

    Returns:
        Dict with keys: priority, reason, action, notify_owner,
        gorgias_priority_set, note_posted, draft_text
    """
    settings = get_settings()
    prompt = _build_prompt(ticket_id, message_text, ticket_subject,
                           customer_email, intents)

    # --yolo auto-approves all tool calls without human confirmation.
    # This is safe because:
    # 1. The 3 registered MCP tools (buttonsbebe_kb, buttonsbebe_redo,
    #    buttonsbebe_gorgias) are ALL read-only (GET only — no POST/PUT/DELETE).
    # 2. The prompt forbids curl/direct API access and returns the draft to the
    #    processor. Only a human-triggered console endpoint may write to Gorgias.
    # 3. `hermes mcp list` confirms exactly 3 tools, all read-only.
    # If a future MCP tool with write capability is added, --yolo would
    # auto-approve it — revisit this decision at that point.
    cmd = [
        "hermes",
        "--yolo",
        "-z", prompt,
    ]

    log_event(logger, "INFO", "Invoking Hermes headless",
              ticket_id=ticket_id,
              prompt_length=len(prompt),
              timeout=settings.job_timeout)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.job_timeout,
            # Ensure Hermes can find its config and skills
            env={
                **dict(__import__("os").environ),
                "HOME": "/root",
                "PATH": "/root/.local/bin:/usr/local/bin:/usr/bin:/bin",
            },
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode != 0:
            log_event(logger, "ERROR", "Hermes exited with non-zero code",
                      ticket_id=ticket_id,
                      returncode=result.returncode,
                      stderr=stderr[:500])
            return dict(_FALLBACK_RESULT)

        if not stdout:
            log_event(logger, "ERROR", "Hermes produced no output",
                      ticket_id=ticket_id,
                      stderr=stderr[:500])
            return dict(_FALLBACK_RESULT)

        # Parse the JSON_RESULT from the output
        parsed = _parse_json_result(stdout)

        # Extract draft text from <DRAFT>...</DRAFT> tags (when writes disabled)
        draft_text = _extract_draft(stdout)
        if draft_text:
            parsed["draft_text"] = draft_text
            log_event(logger, "INFO", "Draft extracted from Hermes output",
                      ticket_id=ticket_id,
                      draft_length=len(draft_text))

        log_event(logger, "INFO", "Hermes processing complete",
                  ticket_id=ticket_id,
                  priority=parsed["priority"],
                  action=parsed["action"],
                  notify_owner=parsed["notify_owner"],
                  gorgias_priority_set=parsed["gorgias_priority_set"],
                  note_posted=parsed["note_posted"])

        # Store the raw output for debugging (first 500 chars)
        parsed["_raw_output_preview"] = stdout[:500]

        return parsed

    except subprocess.TimeoutExpired:
        log_event(logger, "ERROR", "Hermes invocation timed out",
                  ticket_id=ticket_id,
                  timeout=settings.job_timeout)
        return dict(_FALLBACK_RESULT)

    except Exception as exc:
        log_event(logger, "ERROR", f"Hermes invocation failed: {exc}",
                  ticket_id=ticket_id,
                  error_type=type(exc).__name__)
        return dict(_FALLBACK_RESULT)
