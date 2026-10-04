"""QA-only fixture boundaries and strict policy-search projection."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

GROUPS = ("buttonsbebe_kb", "buttonsbebe_redo", "buttonsbebe_gorgias")
TOOLS = {
    "buttonsbebe_kb": {"search_kb"},
    "buttonsbebe_redo": {"list_recent_returns", "get_returns_for_order", "get_return", "get_order"},
    "buttonsbebe_gorgias": {"list_recent_tickets", "list_inbox_tickets", "get_ticket", "get_ticket_messages", "get_customer", "search_customer"},
}
UTILITY_NAMES = {"list_resources", "read_resource", "list_prompts", "get_prompt"}
ALLOWED_CATEGORIES = {"policies", "faq", "intents", "products"}
FILTERED_CATEGORIES = {"tickets", "learned", "notices", "notice", "shopify"}
HEALTH_STATES = {"healthy", "degraded", "unavailable"}
HEALTH_CODES = {
    "notice_store_invalid", "notice_read_failed",
    "index_lock_failed", "index_recovery_failed", "index_open_failed",
    "embedding_failed", "vector_lookup_failed", "keyword_lookup_failed",
    "index_rows_invalid",
}
NOTICE_OPERATOR_ACTIONS = {
    "",
    "Inspect the Notice Board. Active owner overrides may still exist.",
}


def redact(text: str) -> str:
    text = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "[email removed]", text, flags=re.I)
    text = re.sub(r"(?<!\w)\+?\d[\d ().-]{7,}\d(?!\w)", "[phone or identifier removed]", text)
    text = re.sub(r"\b\d{1,6}\s+[A-Za-z0-9 .'-]{1,60}\b(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Drive|Dr)\b", "[address removed]", text, flags=re.I)
    text = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[query removed]", text)
    return text


def policy_files(repo: Path) -> set[str]:
    # Product names enter only through the reviewed external manifest.
    root = repo.resolve() / "kb"
    if root.is_symlink(): raise ValueError("Unsafe KB directory")
    result = set()
    for category in ALLOWED_CATEGORIES - {"products"}:
        directory = root / category
        if directory.is_symlink(): raise ValueError("Unsafe KB category directory")
        for path in directory.glob("*.md"):
            if path.is_symlink() or not path.is_file(): raise ValueError("Unsafe KB policy file")
            result.add(str(path.relative_to(root)))
    return result


def filter_policy_results(rows, allowed_files: set[str]) -> tuple[list[dict], int]:
    if not isinstance(rows, list) or len(rows) > 200:
        raise ValueError("Unexpected KB result collection")
    output = []
    filtered = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Unexpected KB result record")
        category = row.get("category")
        if category in FILTERED_CATEGORIES:
            filtered += 1
            continue
        if category not in ALLOWED_CATEGORIES:
            raise ValueError("Unexpected KB category")
        name = row.get("file")
        if not isinstance(name, str) or name not in allowed_files:
            raise ValueError("Unexpected KB document path")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or path.parts[0] != category:
            raise ValueError("Unexpected KB document path")
        if row.get("status") != "confirmed":
            filtered += 1
            continue
        if any(not isinstance(row.get(field, ""), str) for field in ("title", "text")) or not isinstance(row.get("heading") or "", str):
            raise ValueError("Unexpected KB content shape")
        if len(row["text"]) > 50000:
            raise ValueError("Oversized KB text")
        output.append({"file": name, "category": category, "status": "confirmed",
                       "sensitive": row.get("sensitive") is True,
                       "title": redact(row.get("title", ""))[:300],
                       "heading": redact(row.get("heading") or "")[:300],
                       "text": redact(row["text"])[:10000]})
    return output, filtered


def filter_search_outcome(outcome, allowed_files: set[str]) -> tuple[dict, int]:
    """Validate production health metadata and project only safe KB passages."""
    try:
        if not isinstance(outcome, dict) or set(outcome) != {"status", "notice_board", "index", "results"}:
            raise ValueError("Unexpected KB search outcome")

        notice = outcome["notice_board"]
        index = outcome["index"]
        if not isinstance(notice, dict) or set(notice) != {"state", "codes", "active_count", "operator_action"}:
            raise ValueError("Unexpected Notice Board health")
        if not isinstance(index, dict) or set(index) != {"state", "codes"}:
            raise ValueError("Unexpected index health")

        def validate_health(source, *, is_notice=False):
            state = source["state"]
            codes = source["codes"]
            if state not in HEALTH_STATES or not isinstance(codes, list) or len(codes) > 16:
                raise ValueError("Unexpected KB health value")
            if any(not isinstance(code, str) or code not in HEALTH_CODES for code in codes):
                raise ValueError("Unexpected KB health diagnostic")
            if len(set(codes)) != len(codes):
                raise ValueError("Duplicate KB health diagnostic")
            if (state == "healthy") != (not codes):
                raise ValueError("KB health state does not match its diagnostics")
            if is_notice:
                if state == "degraded":
                    raise ValueError("Unexpected degraded Notice Board health")
                count = source["active_count"]
                action = source["operator_action"]
                if count is not None and (type(count) is not int or not 0 <= count <= 100_000):
                    raise ValueError("Unexpected Notice Board count")
                if (state == "unavailable") != (count is None):
                    raise ValueError("Notice Board count does not match its health")
                if not isinstance(action, str) or action not in NOTICE_OPERATOR_ACTIONS:
                    raise ValueError("Unexpected Notice Board action")
                expected_action = "Inspect the Notice Board. Active owner overrides may still exist." if state == "unavailable" else ""
                if action != expected_action:
                    raise ValueError("Notice Board action does not match its health")
                return {"state": state, "codes": list(codes), "active_count": count,
                        "operator_action": action}
            return {"state": state, "codes": list(codes)}

        safe_notice = validate_health(notice, is_notice=True)
        safe_index = validate_health(index)
        states = (safe_notice["state"], safe_index["state"])
        expected_status = "healthy" if states == ("healthy", "healthy") else (
            "unavailable" if states == ("unavailable", "unavailable") else "degraded"
        )
        if outcome["status"] != expected_status:
            raise ValueError("Search status does not match source health")

        safe_results, filtered = filter_policy_results(outcome["results"], allowed_files)
        return ({"status": expected_status, "notice_board": safe_notice,
                 "index": safe_index, "results": safe_results}, filtered)
    except (KeyError, TypeError) as error:
        raise ValueError("Unexpected KB search outcome") from error


def scenario_fixture(scenario: dict, ordinal: int) -> dict:
    email = scenario.get("email", "")
    if not isinstance(email, str) or not email.endswith("@example.com"):
        raise ValueError("QA scenarios must use example.com addresses")
    ticket_id = 900_000_000 + ordinal
    customer_id = 910_000_000 + ordinal
    numbers = re.findall(r"#(\d+)", scenario.get("message", ""))
    orders = [{"id": "qa-order-" + number, "name": number, "order_name": "#" + number,
               "fulfillment_status": "unfulfilled", "delivery_status": "not_shipped",
               "financial_status": "paid", "tracking": None,
               "qa_fixture": True} for number in dict.fromkeys(numbers)]
    customer = {"id": customer_id, "email": email, "name": "QA Customer", "orders": orders, "qa_fixture": True}
    ticket = {"id": ticket_id, "subject": scenario["subject"], "status": "open", "customer": customer, "qa_fixture": True}
    message = {"id": "qa-message-" + scenario["id"], "body_text": scenario["message"], "from_agent": False,
               "sender": {"id": customer_id, "email": email, "name": "QA Customer"}, "qa_fixture": True}
    return {"scenario_id": scenario["id"], "ticket": ticket, "customer": customer, "messages": [message], "orders": orders, "returns": []}


def validate_fixture(value):
    """Accept only the bounded synthetic shape generated by scenario_fixture.

    Markers are validation, not anonymization: scenario authors must still use
    synthetic text. No arbitrary customer export fields or real identifiers pass.
    """
    try:
        if not isinstance(value,dict) or len(json.dumps(value)) > 100000:
            raise ValueError("Oversized fixture")
        ticket = value['ticket']; customer = value['customer']; messages = value['messages']
        if type(ticket['id']) is not int or not 900000001 <= ticket['id'] <= 900050000 or len(messages) != 1:
            raise ValueError("Non-synthetic fixture identifiers")
        ordinal = ticket['id'] - 900000000
        message = messages[0]
        sid = message['id'].removeprefix('qa-message-')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',sid): raise ValueError("Invalid fixture ID")
        if any(not isinstance(v,str) or len(v)>20000 for v in (ticket['subject'],message['body_text'],customer['email'])):
            raise ValueError("Invalid fixture text")
        expected = scenario_fixture({'id':sid,'subject':ticket['subject'],'message':message['body_text'],'email':customer['email']},ordinal)
        if value.get('scenario_id') == 'QA-PREFLIGHT': expected['scenario_id']='QA-PREFLIGHT'
        if json.dumps(value,sort_keys=True) != json.dumps(expected,sort_keys=True):
            raise ValueError("Fixture differs from synthetic schema")
        return value
    except (KeyError,TypeError,AttributeError) as error:
        raise ValueError("Invalid synthetic fixture") from error


def audit(path: Path, group: str, tool: str, **details):
    # KB audit entries may include only returned snippets after allowlist
    # filtering, redaction and text bounds; raw responses and credentials stay out.
    import os
    payload = json.dumps({"group": group, "tool": tool, **details}, separators=(",", ":")) + "\n"
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, payload.encode())
    finally:
        os.close(fd)
