"""Offline fidelity/restart/rollback rehearsal against a saved export manifest.

No network client imports. All real-data reports remain in the private workspace.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from unittest.mock import patch

from . import import_store
from .imports import account_name
from .policy import Invalid, install_offline_guard, workspace_directory
from .records import canonical
from .store import Store


def read_snapshot(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("format") != "gorgias-mcp-test-snapshot-v1" or not manifest.get("sample_capture_complete"):
        raise Invalid("Rehearsal requires a completed test snapshot manifest.")
    files = []
    for entry in manifest["files"]:
        name = entry["file"]
        if not isinstance(name, str) or Path(name).name != name:
            raise Invalid("Unsafe snapshot filename.")
        raw = (directory / name).read_bytes()
        if sha256(raw).hexdigest() != entry["sha256"]:
            raise Invalid("Snapshot checksum mismatch. Do not import modified evidence.")
        files.append(raw)
    if not files or len(files) != manifest["counts"]["tickets"]:
        raise Invalid("Snapshot file count differs from its manifest.")
    return manifest, files


def database_counts(store):
    with store.connection() as db:
        return {table: db.execute("SELECT count(*) FROM " + table).fetchone()[0]
                for table in ("tickets", "messages", "contacts", "attachments", "events", "source_records", "import_batches")}


def mappings(store):
    with store.connection() as db:
        return [tuple(row) for row in db.execute(
            "SELECT kind,account,external_id,internal_id,ticket_id FROM source_records ORDER BY kind,account,external_id")]


def utc(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def fidelity(store, payloads, account):
    checks = Counter()
    problems = []

    def check(label, condition):
        checks[label] += 1
        if not condition:
            problems.append(label)

    with store.connection() as db:
        for payload in payloads:
            for source in json.loads(payload)["tickets"]:
                source_row = db.execute("SELECT * FROM source_records WHERE kind='ticket' AND account=? AND external_id=?",
                                        (account, str(source["id"]))).fetchone()
                check("ticket_source_mapping", source_row is not None)
                if source_row is None:
                    continue
                ticket = db.execute("SELECT * FROM tickets WHERE id=?", (source_row["internal_id"],)).fetchone()
                original = {k: v for k, v in source.items() if k != "messages"}
                check("complete_ticket_source_record", json.loads(source_row["payload_json"]) == original)
                check("ticket_status", ticket["status"] == source["status"])
                check("ticket_subject", ticket["subject"] == (source.get("subject") or "Untitled ticket").strip())
                check("ticket_timestamps", utc(ticket["created_at"]) == utc(source["created_datetime"]) and
                      utc(ticket["updated_at"]) == utc(source.get("updated_datetime") or source["created_datetime"]))
                priority = source.get("priority") or "normal"
                check("ticket_priority", ticket["priority"] == ("critical" if priority == "urgent" else priority))
                assignee = source.get("assignee_user") or {}
                check("ticket_assignment", ticket["assignee"] == (assignee.get("email") or assignee.get("name") or "").strip())
                check("ticket_tags", json.loads(ticket["tags_json"]) == (source.get("tags") or []))
                for message in source["messages"]:
                    mapped = db.execute("SELECT * FROM source_records WHERE kind='message' AND account=? AND external_id=?",
                                        (account, str(message["id"]))).fetchone()
                    check("message_source_mapping", mapped is not None and mapped["ticket_id"] == ticket["id"])
                    if mapped is None:
                        continue
                    check("complete_message_source_record", json.loads(mapped["payload_json"]) == message)
                    row = db.execute("SELECT * FROM messages WHERE id=?", (mapped["internal_id"],)).fetchone()
                    expected_kind = "note" if message["public"] is False else "outgoing" if message["from_agent"] else "incoming"
                    check("message_visibility_and_direction", row["kind"] == expected_kind)
                    sender = message.get("sender") or {}
                    check("message_author", row["author_name"] == (sender.get("name") or "Unknown author").strip()
                          and row["author_email"] == (sender.get("email") or "").strip())
                    check("message_timestamp", utc(row["created_at"]) == utc(message["created_datetime"]))
                    check("email_headers", json.loads(row["headers_json"]) == (message.get("headers") or {}))
                    if message.get("body_text") is not None:
                        check("message_display_text", row["body"] == message["body_text"].strip())
                    else:
                        checks["html_only_display_requires_manual_review"] += 1
                    saved_attachments = [r[0] for r in db.execute("SELECT metadata_json FROM attachments WHERE message_id=?", (row["id"],))]
                    check("attachment_metadata", Counter(saved_attachments) == Counter(canonical(a) for a in (message.get("attachments") or [])))
    return {"checks": dict(checks), "failures": dict(Counter(problems)), "passed": not problems}


def rehearse(directory, workspace, account):
    manifest, files = read_snapshot(directory)
    account = account_name(account)
    store = Store(workspace_directory(workspace))
    if database_counts(store)["tickets"]:
        raise Invalid("Use a new empty workspace for this reconciliation rehearsal.")
    baseline = database_counts(store)
    # Prove a failure at the end of a real conversation import rolls back every
    # table before retrying the exact saved file. No runtime worker is involved.
    first = import_store.preview(store, files[0], account)
    with patch.object(store, "event", side_effect=sqlite3.OperationalError("simulated interruption")):
        try:
            import_store.apply_import(store, files[0], account, first["digest"])
        except sqlite3.OperationalError:
            pass
        else:
            raise Invalid("Interruption simulation did not run.")
    if database_counts(store) != baseline:
        raise Invalid("Interrupted import left partial records.")
    for payload in files:
        preview = import_store.preview(store, payload, account)
        import_store.apply_import(store, payload, account, preview["digest"])
    first_counts, first_mappings = database_counts(store), mappings(store)
    checked = fidelity(store, files, account)
    store = Store(workspace_directory(workspace))
    for payload in files:
        preview = import_store.preview(store, payload, account)
        result = import_store.apply_import(store, payload, account, preview["digest"])
        if not result["alreadyImported"] or any(result["new"].values()):
            raise Invalid("Repeated import unexpectedly created records.")
    counts_match = all(first_counts[key] == manifest["counts"][key] for key in ("tickets", "messages", "attachments"))
    stable = first_counts == database_counts(store) and first_mappings == mappings(store)
    report = {"source_snapshot": str(directory), "workspace": workspace,
              "source_counts": manifest["counts"], "database_counts": first_counts,
              "counts_match": counts_match, "fidelity": checked,
              "interrupted_import_rolled_back": True, "restart_and_repeat_stable": stable,
              "outbound_capability": False,
              "limitations": ["Bounded sample, not whole-account migration.", "Attachments are metadata only.",
                              "No private source notes in this sample." if not manifest["counts"]["notes"] else "Owner review remains pending."],
              "passed": counts_match and stable and checked["passed"]}
    report_path = workspace_directory(workspace) / "reconciliation.json"
    report_path.write_text(json.dumps(report, indent=2))
    os.chmod(report_path, 0o600)
    return report, report_path


def main():
    parser = argparse.ArgumentParser(description="Offline rehearsal on a completed saved snapshot; no provider access.")
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--account", required=True)
    args = parser.parse_args()
    install_offline_guard()
    os.umask(0o077)
    try:
        report, path = rehearse(args.snapshot, args.workspace, args.account)
    except (Invalid, OSError, ValueError, KeyError, sqlite3.Error):
        print("Rehearsal failed. Inspect the private source and workspace; no live action was attempted.")
        return 1
    print(json.dumps({"passed": report["passed"], "counts": report["database_counts"],
                      "field_checks": sum(report["fidelity"]["checks"].values()),
                      "fidelity_failures": report["fidelity"]["failures"],
                      "restart_and_repeat_stable": report["restart_and_repeat_stable"],
                      "interrupted_import_rolled_back": report["interrupted_import_rolled_back"],
                      "report": str(path)}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
