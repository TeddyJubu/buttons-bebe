"""Dashboard read APIs and processor result ingestion."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictBool, field_validator, model_validator

from .. import deps

router = APIRouter(prefix="/dashboard/api")


@router.get("/messages")
async def dashboard_messages(
    limit: int = 50,
    offset: int = 0,
    customer_only: bool = False,
) -> JSONResponse:
    """Return parsed messages as JSON."""
    messages = await deps.database_function("get_parsed_messages")(
        limit=max(1, min(limit, 200)),
        offset=max(0, offset),
        customer_only=customer_only,
    )
    return JSONResponse(content=messages)


@router.get("/stats")
async def dashboard_stats() -> JSONResponse:
    """Return aggregate processing statistics."""
    return JSONResponse(content=await deps.database_function("get_result_stats")())


@router.get("/tickets")
async def dashboard_tickets_api(limit: int = 100, offset: int = 0) -> JSONResponse:
    """Return customer messages joined with AI processing results."""
    tickets = await deps.database_function("get_dashboard_tickets")(
        limit=max(1, min(limit, 500)),
        offset=max(0, offset),
    )
    return JSONResponse(content=tickets)


@router.get("/owner-alerts")
async def dashboard_owner_alerts(limit: int = 50, offset: int = 0) -> JSONResponse:
    """Read-only inspection of owner-alert attempts needing attention."""
    return JSONResponse(content=await deps.database_function("get_owner_alerts")(
        limit=limit, offset=offset,
    ))


class ResultPayload(BaseModel):
    """Declarative contract for the processor result seam (3.6).

    Error keys stay byte-identical to the hand validation this replaces:
    the processor treats any non-ok acknowledgement as fatal, and the
    adversarial console suite pins missing_fields/invalid_* strings.
    """

    model_config = {"extra": "ignore"}

    ticket_id: int = Field(gt=0, le=9_223_372_036_854_775_807)
    message_id: str | int = Field(max_length=128)
    job_id: int | None = Field(default=None, gt=0)
    priority: Literal["critical", "high", "normal", "low"]
    action: Literal["drafted", "sensitive_draft", "escalated", "no_kb_match", "no_draft_needed"]
    reason: str = Field(default="", max_length=2_000)
    draft_text: str | None = Field(default=None, max_length=100_000)
    notify_owner: StrictBool = False
    gorgias_priority_set: StrictBool = False
    note_posted: StrictBool = False
    generation_attempt_id: int | None = Field(default=None, gt=0)
    generation_state: Literal['ready','needs_review','no_reply','failed'] | None = None
    generation_error: Literal['timeout','process_exit','runtime_error','authentication','invalid_output','safety_rejected'] | None = None
    review_required: StrictBool = False
    staff_next_step: str = Field(default='', max_length=1000)
    missing_facts: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def _coherent_generation(self):
        if self.generation_attempt_id is None and (
                self.generation_state is not None or self.generation_error is not None
                or self.review_required or self.staff_next_step or self.missing_facts):
            raise ValueError('generation_attempt_required_for_metadata')
        if self.generation_attempt_id is not None:
            if not self.job_id or not self.generation_state:
                raise ValueError('generation_identity_and_state_required')
            if self.generation_state == 'failed' and not self.generation_error:
                raise ValueError('failed_generation_error_required')
            if self.generation_state == 'needs_review' and not self.staff_next_step.strip():
                raise ValueError('staff_task_required')
        return self

    @field_validator('missing_facts')
    @classmethod
    def _bounded_facts(cls, value):
        if any(not fact.strip() or len(fact) > 200 for fact in value):
            raise ValueError('invalid_missing_facts')
        return value

    @field_validator("ticket_id", "job_id", mode="before")
    @classmethod
    def _reject_non_int_id(cls, value):
        # Legacy hand validation accepted only type(value) is int
        # (job_id additionally allows absent/None).
        if value is None:
            return None
        if type(value) is not int:
            raise ValueError("not an id")
        return value

    @field_validator("message_id", mode="before")
    @classmethod
    def _coerce_message_id(cls, value):
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ValueError("invalid_message_id")
        text = str(value)
        if not text.strip() or len(text) > 128:
            raise ValueError("invalid_message_id")
        return text


# pydantic field name -> the legacy error key the suite pins.
_FIELD_ERRORS = {
    "ticket_id": "invalid_ticket_id",
    "message_id": "invalid_message_id",
    "job_id": "invalid_job_id",
    "priority": "invalid_priority",
    "action": "invalid_action",
    "reason": "invalid_reason",
    "draft_text": "invalid_draft_text",
    "notify_owner": "invalid_notify_owner",
    "gorgias_priority_set": "invalid_gorgias_priority_set",
    "note_posted": "invalid_note_posted",
}


@router.post("/results")
async def record_result_api(request: Request) -> JSONResponse:
    """Record a Hermes result posted by the processor."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_json"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"error": "invalid_json_object"})

    required = {"ticket_id", "message_id", "priority", "action"}
    if not required.issubset(body.keys()):
        return JSONResponse(
            status_code=400,
            content={"error": "missing_fields", "required": sorted(required)},
        )

    try:
        payload = ResultPayload.model_validate(body)
    except Exception as exc:
        first = exc.errors()[0] if hasattr(exc, "errors") else {}
        field = str(first.get("loc", [""])[0] if first.get("loc") else "")
        key = _FIELD_ERRORS.get(field, f"invalid_{field}" if field else "invalid_request")
        return JSONResponse(status_code=400, content={"error": key})

    if payload.generation_attempt_id is not None:
        from ..draft_generation import finish_attempt
        from ..send_intents import ActionConflict
        try:
            state = await finish_attempt(payload.model_dump(), deps.get_db())
        except ActionConflict as exc:
            return JSONResponse(status_code=exc.status, content={'error': exc.error})
        return JSONResponse(content={'status': 'ok', 'generation_state': state})

    await deps.database_function("record_ticket_result")(
        ticket_id=payload.ticket_id,
        message_id=str(payload.message_id),
        job_id=payload.job_id,
        priority=payload.priority,
        action=payload.action,
        reason=payload.reason,
        notify_owner=payload.notify_owner,
        gorgias_priority_set=payload.gorgias_priority_set,
        note_posted=payload.note_posted,
        draft_text=payload.draft_text,
    )
    return JSONResponse(content={"status": "ok"})


@router.get("/ops")
async def dashboard_ops() -> JSONResponse:
    """Monitor summary; dashboard session middleware protects this route."""
    from ..ops_status import summary
    return JSONResponse(content=summary(), headers={"Cache-Control": "no-store"})
