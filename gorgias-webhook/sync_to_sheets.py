#!/usr/bin/env python3
"""
sync_to_sheets.py — Loop importer: CSV export → Google Sheets (batched append).

Reads rows from export_tickets.py CSV output and appends them to a Google
Spreadsheet in batches. Tracks progress in a state file so you can re-run
safely or run in a continuous loop until fully synced.

Prerequisites:
  1. Run export first:  python3 export_tickets.py
  2. Google OAuth set up:
       python3 ~/.hermes/skills/productivity/google-workspace/scripts/setup.py --check
     If NOT_AUTHENTICATED, follow the google-workspace skill setup (Steps 2-5).

Usage:
  # One-shot: create sheet + import messages CSV
  python3 sync_to_sheets.py --csv exports/messages_12mo_2026-06-26.csv

  # Import into an existing spreadsheet
  python3 sync_to_sheets.py --csv exports/messages_12mo_2026-06-26.csv \\
      --sheet-id SPREADSHEET_ID --tab Messages

  # Loop until fully synced (e.g. while export is still running)
  python3 sync_to_sheets.py --csv exports/messages_12mo_2026-06-26.csv \\
      --loop --interval 120

  # Import ticket summaries instead
  python3 sync_to_sheets.py --csv exports/tickets_12mo_2026-06-26.csv --tab Tickets
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
GOOGLE_SCRIPTS = HERMES_HOME / "skills" / "productivity" / "google-workspace" / "scripts"
TOKEN_PATH = HERMES_HOME / "google_token.json"
STATE_DIR = SCRIPT_DIR / "exports" / ".state"

logger = logging.getLogger("sync-to-sheets")

DEFAULT_BATCH_SIZE = 200
SHEETS_VALUE_LIMIT = 10_000_000  # Google Sheets cell limit guard


def _ensure_google_auth():
    if not TOKEN_PATH.exists():
        setup = GOOGLE_SCRIPTS / "setup.py"
        die(
            f"Google not authenticated (no token at {TOKEN_PATH}).\n"
            f"Run: python3 {setup} --check\n"
            "Then complete OAuth setup per the google-workspace skill."
        )
    if str(GOOGLE_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(GOOGLE_SCRIPTS))
    try:
        from google_api import build_service, get_credentials  # noqa: F401
        get_credentials()
    except Exception as exc:
        die(f"Google auth failed: {exc}")


def die(msg: str, code: int = 1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _state_path(csv_path: Path, sheet_id: str) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    key = f"{csv_path.stem}_{sheet_id[:8]}.json"
    return STATE_DIR / key


def _load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_state(path: Path, state: dict) -> None:
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _read_csv_rows(csv_path: Path) -> tuple[list[str], list[list[str]]]:
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            return [], []
        rows = [row for row in reader]
    return header, rows


def _create_spreadsheet(title: str, tab_name: str) -> dict:
    sys.path.insert(0, str(GOOGLE_SCRIPTS))
    from google_api import build_service

    service = build_service("sheets", "v4")
    body = {
        "properties": {"title": title},
        "sheets": [{"properties": {"title": tab_name}}],
    }
    result = service.spreadsheets().create(
        body=body, fields="spreadsheetId,properties,spreadsheetUrl",
    ).execute()
    return {
        "spreadsheetId": result["spreadsheetId"],
        "title": result.get("properties", {}).get("title", title),
        "spreadsheetUrl": result.get("spreadsheetUrl", ""),
    }


def _append_batch(sheet_id: str, tab: str, values: list[list]) -> int:
    if not values:
        return 0
    sys.path.insert(0, str(GOOGLE_SCRIPTS))
    from google_api import build_service

    service = build_service("sheets", "v4")
    result = service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"{tab}!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": values},
    ).execute()
    return int(result.get("updates", {}).get("updatedCells", 0))


def _sheet_has_header(sheet_id: str, tab: str) -> bool:
    sys.path.insert(0, str(GOOGLE_SCRIPTS))
    from google_api import build_service

    service = build_service("sheets", "v4")
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"{tab}!A1:A1",
        ).execute()
        return bool(result.get("values"))
    except Exception:
        return False


def sync_csv_to_sheets(
    *,
    csv_path: Path,
    sheet_id: str | None,
    tab: str,
    batch_size: int,
    create_title: str | None,
    dry_run: bool,
) -> dict:
    if not csv_path.exists():
        die(f"CSV not found: {csv_path}")

    header, all_rows = _read_csv_rows(csv_path)
    if not header:
        return {"status": "empty", "csv": str(csv_path), "rows_synced": 0}

    if sheet_id is None:
        if dry_run:
            return {
                "status": "dry_run",
                "would_create": create_title or f"Hermes KB — {csv_path.stem}",
                "tab": tab,
                "total_rows": len(all_rows),
            }
        _ensure_google_auth()
        created = _create_spreadsheet(create_title or f"Hermes KB — {csv_path.stem}", tab)
        sheet_id = created["spreadsheetId"]
        logger.info("Created spreadsheet: %s", created.get("spreadsheetUrl"))
    else:
        _ensure_google_auth()

    state_file = _state_path(csv_path, sheet_id)
    state = _load_state(state_file)
    start_row = int(state.get("rows_synced") or 0)
    header_written = bool(state.get("header_written"))

    total = len(all_rows)
    rows_synced = start_row
    cells_updated = 0

    if start_row >= total and header_written:
        return {
            "status": "already_complete",
            "sheet_id": sheet_id,
            "tab": tab,
            "csv": str(csv_path),
            "rows_synced": rows_synced,
            "total_rows": total,
        }

    # Write header once
    if not header_written and not _sheet_has_header(sheet_id, tab):
        if dry_run:
            logger.info("DRY RUN: would write header (%s columns)", len(header))
        else:
            cells_updated += _append_batch(sheet_id, tab, [header])
        header_written = True
        _save_state(state_file, {
            "sheet_id": sheet_id,
            "tab": tab,
            "csv": str(csv_path),
            "header_written": True,
            "rows_synced": rows_synced,
            "total_rows": total,
            "status": "in_progress",
        })

    pending = all_rows[start_row:]
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset: offset + batch_size]
        if dry_run:
            logger.info("DRY RUN: would append %s rows (offset %s)", len(batch), start_row + offset)
            rows_synced += len(batch)
            continue

        cells_updated += _append_batch(sheet_id, tab, batch)
        rows_synced += len(batch)
        _save_state(state_file, {
            "sheet_id": sheet_id,
            "tab": tab,
            "csv": str(csv_path),
            "header_written": header_written,
            "rows_synced": rows_synced,
            "total_rows": total,
            "status": "in_progress" if rows_synced < total else "complete",
            "cells_updated_last_batch": cells_updated,
        })
        logger.info("Synced %s / %s rows (%s cells this batch)", rows_synced, total, cells_updated)
        time.sleep(1.0)  # gentle rate limit for Sheets API

    # Re-read CSV in case export grew while we were syncing (loop mode)
    _, refreshed = _read_csv_rows(csv_path)
    final_total = len(refreshed)
    status = "complete" if rows_synced >= final_total else "partial"

    result = {
        "status": status,
        "sheet_id": sheet_id,
        "tab": tab,
        "csv": str(csv_path),
        "rows_synced": rows_synced,
        "total_rows": final_total,
        "cells_updated": cells_updated,
        "spreadsheet_url": f"https://docs.google.com/spreadsheets/d/{sheet_id}",
    }
    _save_state(state_file, {**result, "header_written": header_written})
    return result


def run_loop(args) -> None:
    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = SCRIPT_DIR / csv_path

    sheet_id = args.sheet_id
    iteration = 0
    while True:
        iteration += 1
        logger.info("=== Sync loop iteration %s ===", iteration)
        result = sync_csv_to_sheets(
            csv_path=csv_path,
            sheet_id=sheet_id,
            tab=args.tab,
            batch_size=args.batch_size,
            create_title=args.create_title,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, indent=2))

        sheet_id = result.get("sheet_id") or sheet_id
        if result.get("status") in ("complete", "already_complete", "empty"):
            logger.info("Sync complete.")
            break
        if not args.loop:
            break
        logger.info("Sleeping %ss before next iteration...", args.interval)
        time.sleep(args.interval)


def main():
    parser = argparse.ArgumentParser(description="Import Gorgias CSV export into Google Sheets")
    parser.add_argument("--csv", required=True, help="Path to messages_*.csv or tickets_*.csv")
    parser.add_argument("--sheet-id", default=None, help="Existing Google Spreadsheet ID")
    parser.add_argument("--tab", default="Messages", help="Worksheet tab name")
    parser.add_argument("--create-title", default=None,
                        help="Title when creating a new spreadsheet")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"Rows per append batch (default {DEFAULT_BATCH_SIZE})")
    parser.add_argument("--loop", action="store_true",
                        help="Re-run until CSV is fully synced (useful while export runs)")
    parser.add_argument("--interval", type=int, default=120,
                        help="Seconds between loop iterations (default 120)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be synced without calling Google APIs")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    run_loop(args)


if __name__ == "__main__":
    main()
