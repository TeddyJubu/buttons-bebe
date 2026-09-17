"""Hermes subprocess orchestration and console-facing result handling."""

from __future__ import annotations

import os
import subprocess
from typing import Any

from config import get_settings
from draft_cleaner import clean_draft, should_draft
from logging_setup import get_logger, log_event
from shared.priority import RANK
from shared.review_policy import final_review_result

from .process import run_bounded

from .constants import (
    _FALLBACK_RESULT,
    _MAX_NOTE,
    _MAX_REASON_WITH_NOTE,
    _MAX_VERDICT_CANDIDATES,
    _MIN_NOTE,
    _NO_DRAFT_RESULT,
    _NOTE_LABEL,
    _make_run_token,
    _token_failure_result,
)
from .extract import _extract_draft_details, _parse_json_result, _valid_verdicts
from .prompt import _build_prompt


logger = get_logger(__name__)


def build_hermes_command(prompt: str, settings: Any) -> list[str]:
    """Build Hermes invocation with the configured read-only tool allow-list."""

    command = [str(getattr(settings, "hermes_bin", "hermes") or "hermes")]
    profile = str(getattr(settings, "hermes_profile", "") or "").strip()
    if profile:
        command += ["-p", profile]
    if bool(getattr(settings, "hermes_ignore_rules", False)):
        command.append("--ignore-rules")

    toolsets = str(getattr(settings, "hermes_toolsets", "") or "").strip()
    wanted = [name.strip() for name in toolsets.split(",")]
    allowed = {"buttonsbebe_kb", "buttonsbebe_redo", "buttonsbebe_gorgias"}
    if len(wanted) != 3 or set(wanted) != allowed:
        raise ValueError("Hermes requires exactly the three approved read-only toolsets")
    command += ["-t", ",".join(wanted)]

    if getattr(settings, "hermes_skip_approval", False):
        log_event(
            logger,
            "WARNING",
            "Hermes running with --yolo (HERMES_SKIP_APPROVAL=1) — "
            "approval prompts are being skipped; this should be temporary",
        )
        command.append("--yolo")
    command += ["-z", prompt]
    return command


def draft_for_console(hermes_result: dict[str, Any]) -> str:
    """Return only a reviewable draft; never synthesize one for no-draft results."""

    reviewed = final_review_result(hermes_result)
    if reviewed.get("no_draft"):
        return ""
    draft = reviewed["draft_text"]
    return draft if draft else str(_FALLBACK_RESULT["draft_text"])



# ADR-015 §2.3 — auth anomalies are non-sendable; runner failures fall back.
def _authentication_failure(reason: str) -> dict[str, Any]:
    """Log and return the distinct non-sendable authentication result."""

    log_event(logger, "WARNING", reason)
    return _token_failure_result(reason)


def _no_draft_result(parsed: dict[str, Any], reason: str) -> dict[str, Any]:
    """Keep authenticated-but-unreviewable drafts elevated and non-sendable."""

    result = dict(parsed)
    if RANK.get(str(result.get("priority", "")).lower().strip(), -1) < RANK["high"]:
        result["priority"] = "high"
    result["action"] = "sensitive_draft"
    result["notify_owner"] = True
    result["gorgias_priority_set"] = False
    result["note_posted"] = False
    result["draft_text"] = ""
    result["no_draft"] = True
    result["reason"] = reason
    return result


def _run_environment(settings: Any) -> dict[str, str]:
    """Build the Hermes environment without loading credentials in this module."""

    # Model-provider credentials are necessary; commerce, session, webhook,
    # WhatsApp and Python/loader startup variables must never cross this boundary.
    allowed = {"LANG", "LC_ALL", "TERM", "OLLAMA_API_KEY", "OPENAI_API_KEY"}
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    hermes_home = str(getattr(settings, "hermes_home", "/root") or "").strip()
    hermes_path = str(
        getattr(
            settings,
            "hermes_path",
            "/root/.local/bin:/usr/local/bin:/usr/bin:/bin",
        )
        or ""
    ).strip()
    if hermes_home:
        environment["HOME"] = hermes_home
    if hermes_path:
        environment["PATH"] = hermes_path
    return environment


def process_ticket_with_hermes(
    ticket_id: int,
    message_text: str,
    ticket_subject: str,
    customer_email: str,
    intents: list,
) -> dict[str, Any]:
    """Invoke Hermes, authenticate its output, and return a console result."""

    gate = should_draft(message_text, ticket_subject)
    if not gate.ok:
        log_event(
            logger,
            "INFO",
            "Skipping draft — nothing to answer",
            ticket_id=ticket_id,
            gate_reason=gate.reason,
        )
        skipped = dict(_NO_DRAFT_RESULT)
        skipped["reason"] = f"No draft generated — {gate.reason}"
        return skipped

    settings = get_settings()
    run_token = _make_run_token()
    prompt = _build_prompt(
        ticket_id,
        message_text,
        ticket_subject,
        customer_email,
        intents,
        run_token,
        getattr(settings, "support_store_name", "Buttons Bebe"),
    )
    try:
        command = build_hermes_command(prompt, settings)
        log_event(
            logger,
            "INFO",
            "Invoking Hermes headless",
            ticket_id=ticket_id,
            prompt_length=len(prompt),
            hermes_flags=command[1:-1],
            timeout=settings.job_timeout,
        )

        result = run_bounded(
            command,
            capture_output=True,
            text=True,
            timeout=settings.job_timeout,
            env=_run_environment(settings),
        )
        stdout = str(result.stdout or "").strip()
        if result.returncode != 0:
            log_event(
                logger,
                "ERROR",
                "Hermes exited with non-zero code",
                ticket_id=ticket_id,
                returncode=result.returncode,
            )
            return dict(_FALLBACK_RESULT)
        if not stdout:
            return _authentication_failure(
                "Hermes produced empty output — no run-token authentication; "
                "no draft stored"
            )

        blocks, json_markers = _valid_verdicts(stdout, token=run_token)
        draft_info = _extract_draft_details(stdout, token=run_token)
        if json_markers > _MAX_VERDICT_CANDIDATES or draft_info.overflow:
            return _authentication_failure(
                "Hermes output exceeded the run-token marker limit — no draft stored"
            )
        if not json_markers or not blocks:
            return _authentication_failure(
                "Hermes emitted no valid run-token JSON_RESULT — no draft stored"
            )
        if len(blocks) != json_markers:
            return _authentication_failure(
                "Hermes emitted malformed run-token JSON_RESULT markers — "
                "no draft stored"
            )
        if draft_info.malformed:
            return _authentication_failure(
                "Hermes emitted malformed run-token DRAFT markers — no draft stored"
            )
        if not draft_info.text:
            return _authentication_failure(
                "Hermes emitted no valid run-token DRAFT — no draft stored"
            )
        if draft_info.ambiguous:
            return _authentication_failure(
                "Hermes emitted multiple run-token drafts — no draft stored"
            )

        parsed = _parse_json_result(stdout, token=run_token)
        if parsed.get("no_draft"):
            return _authentication_failure(
                "Hermes verdict failed run-token normalization — no draft stored"
            )
        draft_text = draft_info.text
        cleaned = clean_draft(draft_text)
        if cleaned.reasons:
            log_event(
                logger,
                "INFO",
                "Draft cleaned before review",
                ticket_id=ticket_id,
                clean_reasons=cleaned.reasons,
                length_before=len(draft_text),
                length_after=len(cleaned.text),
            )
        if cleaned.no_draft:
            log_event(
                logger,
                "WARNING",
                "Draft rejected by last-mile safety cleaning — storing no draft",
                ticket_id=ticket_id,
                clean_reasons=cleaned.reasons,
            )
            parsed = _no_draft_result(
                parsed,
                "Hermes draft rejected by safety cleaning — "
                + "; ".join(cleaned.reasons),
            )
        else:
            parsed["draft_text"] = cleaned.text
            if cleaned.reasons:
                parsed["clean_reasons"] = list(cleaned.reasons)
            if cleaned.removed_note:
                note = " ".join(cleaned.removed_note.split())
                note = note.replace("[", "(").replace("]", ")")
                base = str(parsed.get("reason", ""))
                overhead = len(_NOTE_LABEL) + 5
                budget = max(_MIN_NOTE, _MAX_REASON_WITH_NOTE - len(base) - overhead)
                note = note[:min(budget, _MAX_NOTE)]
                parsed["reason"] = f"{base} [{_NOTE_LABEL}: {note}]".strip()
            log_event(
                logger,
                "INFO",
                "Draft extracted from Hermes output",
                ticket_id=ticket_id,
                draft_length=len(cleaned.text),
            )

        log_event(
            logger,
            "INFO",
            "Hermes processing complete",
            ticket_id=ticket_id,
            priority=parsed["priority"],
            action=parsed["action"],
            notify_owner=parsed["notify_owner"],
            gorgias_priority_set=parsed["gorgias_priority_set"],
            note_posted=parsed["note_posted"],
        )
        return final_review_result(parsed)

    except subprocess.TimeoutExpired:
        log_event(
            logger,
            "ERROR",
            "Hermes invocation timed out",
            ticket_id=ticket_id,
            timeout=settings.job_timeout,
        )
        return dict(_FALLBACK_RESULT)
    except Exception as exc:  # noqa: BLE001 - queue loop must fail soft
        log_event(
            logger,
            "ERROR",
            "Hermes invocation failed",
            ticket_id=ticket_id,
            error_type=type(exc).__name__,
        )
        return dict(_FALLBACK_RESULT)
