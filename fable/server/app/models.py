"""Pydantic request models (shapes from API contract §1)."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


# --- Intake ---------------------------------------------------------------
class EmailIntake(BaseModel):
    from_email: str
    from_name: Optional[str] = None
    subject: Optional[str] = None
    body_text: str
    message_id: Optional[str] = None


class ChatIntake(BaseModel):
    session_id: str
    name: Optional[str] = None
    email: Optional[str] = None
    body_text: str


class WhatsappIntake(BaseModel):
    phone: str
    name: Optional[str] = None
    body_text: str


# --- Actions --------------------------------------------------------------
class SendBody(BaseModel):
    text: str


class NoteBody(BaseModel):
    text: str


class RewriteBody(BaseModel):
    instruction: str


class PatchTicketBody(BaseModel):
    status: Optional[str] = None
    assignee: Optional[str] = None
    tags: Optional[List[str]] = None
    snooze_until: Optional[str] = None


# --- Gorgias-compat write --------------------------------------------------
class GorgiasMessageBody(BaseModel):
    channel: Optional[str] = "internal"
    body_text: str
    body_html: Optional[str] = None
    public: Optional[bool] = None
    from_agent: Optional[bool] = True
    sender: Optional[dict] = None
