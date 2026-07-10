"""Email transport interface — plumbing for the Sprint 3 cutover.

Fable talks to email through a small ``EmailTransport`` interface with two
implementations:

* ``MailboxEmulatorTransport`` — the world Fable lives in today. It "sends" by
  posting to the local mailbox emulator (which just files the message in an
  outbox — nothing ever leaves localhost). This mirrors what
  ``app/actions.py`` already does; it does **not** replace those call sites.
* ``ImapSmtpTransport`` — a real-world skeleton that would fetch incoming mail
  over IMAP and send replies over SMTP using the Python standard library. It is
  built lazily: constructing one opens **no** network connection; a socket is
  only touched the first time you actually call ``fetch_new()`` or ``send()``.

``select_transport()`` picks between them from the environment variable
``FABLE_EMAIL_TRANSPORT`` (``emulator`` — the default — or ``imap``).

Nothing in this module sends anything by itself; a transport only acts when a
caller explicitly invokes ``send()``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, runtime_checkable

from . import config


class EmailTransportError(RuntimeError):
    """Raised when a transport cannot deliver or fetch mail."""


@dataclass
class IncomingEmail:
    """One inbound email, in the shape Fable's email intake expects."""
    from_email: str
    from_name: str = ""
    subject: str = ""
    body_text: str = ""
    message_id: Optional[str] = None
    headers: dict = field(default_factory=dict)

    def as_intake_payload(self) -> dict:
        return {
            "from_email": self.from_email,
            "from_name": self.from_name,
            "subject": self.subject,
            "body_text": self.body_text,
            "message_id": self.message_id,
        }


@runtime_checkable
class EmailTransport(Protocol):
    """What every email transport must provide."""

    name: str

    def fetch_new(self) -> List[IncomingEmail]:
        """Return inbound emails that have not been handled yet."""
        ...

    def send(self, *, to: str, subject: str, body_text: str,
             body_html: Optional[str] = None, **extra) -> dict:
        """Deliver one outbound email. Returns a small result dict."""
        ...


# --------------------------------------------------------------- emulator ----
class MailboxEmulatorTransport:
    """Send through the local mailbox emulator (today's behaviour).

    ``send()`` POSTs to ``{base_url}/send`` exactly like ``app/actions.py`` does.
    Incoming email is *pushed* into Fable by the emulator's ``/simulate/incoming``
    endpoint (it forwards straight to the intake API), so there is nothing to
    *pull*: ``fetch_new()`` returns an empty list.

    ``http_post`` can be injected in tests to route the call to an in-process
    ``TestClient`` — no sockets required.
    """

    name = "emulator"

    def __init__(self, base_url: Optional[str] = None, *, http_post=None,
                 timeout: float = 5.0):
        # base_url="" is valid and useful in tests (posts a relative "/send").
        self.base_url = (config.MAILBOX_BASE if base_url is None else base_url).rstrip("/")
        self.timeout = timeout
        self._post = http_post or self._default_post

    @staticmethod
    def _default_post(url, **kwargs):
        import httpx
        # trust_env=False: the mailbox is a localhost service — never route
        # this through an environment proxy.
        return httpx.post(url, trust_env=False, **kwargs)

    def fetch_new(self) -> List[IncomingEmail]:
        # Incoming mail is delivered by the emulator's push endpoint, not pulled.
        return []

    def send(self, *, to: str, subject: str, body_text: str,
             body_html: Optional[str] = None, **extra) -> dict:
        payload = {"to": to, "subject": subject, "body_text": body_text}
        if body_html is not None:
            payload["body_html"] = body_html
        for k in ("ticket_id", "in_reply_to"):
            if k in extra:
                payload[k] = extra[k]
        try:
            resp = self._post(f"{self.base_url}/send", json=payload, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001
            raise EmailTransportError(f"mailbox transport unreachable: {exc!r}") from exc
        status = getattr(resp, "status_code", 0)
        if status // 100 != 2:
            raise EmailTransportError(f"mailbox transport error: HTTP {status}")
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = {}
        return {"ok": True, "transport": self.name, "response": body}


# --------------------------------------------------------------- imap/smtp ---
class ImapSmtpTransport:
    """Real IMAP+SMTP transport skeleton (used from Sprint 3 onwards).

    Constructing this opens no connection. The IMAP and SMTP clients are created
    lazily the first time ``fetch_new()`` / ``send()`` are called, so it is safe
    to build (and unit-test) with any host — nothing dials out until you use it.
    """

    name = "imap"

    def __init__(self, *, imap_host: str, smtp_host: str,
                 imap_port: int = 993, smtp_port: int = 465,
                 user: str = "", password: str = "",
                 support_email: Optional[str] = None,
                 use_ssl: bool = True, timeout: float = 30.0,
                 mailbox: str = "INBOX"):
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.user = user
        self.password = password
        self.support_email = support_email or user
        self.use_ssl = use_ssl
        self.timeout = timeout
        self.mailbox = mailbox
        # Connections are created on first use — NOT here.
        self._imap = None
        self._smtp = None

    # -- lazy connection openers (only called from fetch_new / send) ---------
    def _connect_imap(self):
        if self._imap is None:
            import imaplib
            if self.use_ssl:
                self._imap = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
            else:
                self._imap = imaplib.IMAP4(self.imap_host, self.imap_port)
            if self.user:
                self._imap.login(self.user, self.password)
        return self._imap

    def _connect_smtp(self):
        if self._smtp is None:
            import smtplib
            if self.use_ssl:
                self._smtp = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port,
                                              timeout=self.timeout)
            else:
                self._smtp = smtplib.SMTP(self.smtp_host, self.smtp_port,
                                          timeout=self.timeout)
                self._smtp.starttls()
            if self.user:
                self._smtp.login(self.user, self.password)
        return self._smtp

    def fetch_new(self) -> List[IncomingEmail]:
        """Fetch UNSEEN messages from the mailbox and return them parsed.

        Opens the IMAP connection on first call.
        """
        import email as email_mod
        from email.header import decode_header, make_header

        imap = self._connect_imap()
        imap.select(self.mailbox)
        typ, data = imap.search(None, "UNSEEN")
        if typ != "OK" or not data or not data[0]:
            return []
        out: List[IncomingEmail] = []
        for num in data[0].split():
            typ, msg_data = imap.fetch(num, "(RFC822)")
            if typ != "OK" or not msg_data:
                continue
            raw = msg_data[0][1]
            msg = email_mod.message_from_bytes(raw)
            out.append(self._parse_message(msg, make_header, decode_header))
        return out

    @staticmethod
    def _parse_message(msg, make_header, decode_header) -> IncomingEmail:
        def _hdr(name):
            val = msg.get(name, "")
            try:
                return str(make_header(decode_header(val))) if val else ""
            except Exception:  # noqa: BLE001
                return val or ""

        from email.utils import parseaddr
        name, addr = parseaddr(_hdr("From"))
        body_text = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        body_text = payload.decode(part.get_content_charset() or "utf-8",
                                                   errors="replace")
                        break
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                body_text = payload.decode(msg.get_content_charset() or "utf-8",
                                           errors="replace")
        return IncomingEmail(
            from_email=addr, from_name=name, subject=_hdr("Subject"),
            body_text=body_text, message_id=_hdr("Message-ID") or None,
        )

    def send(self, *, to: str, subject: str, body_text: str,
             body_html: Optional[str] = None, **extra) -> dict:
        """Send one email over SMTP. Opens the SMTP connection on first call."""
        from email.message import EmailMessage

        em = EmailMessage()
        em["From"] = self.support_email
        em["To"] = to
        em["Subject"] = subject or ""
        if extra.get("in_reply_to"):
            em["In-Reply-To"] = extra["in_reply_to"]
        em.set_content(body_text or "")
        if body_html is not None:
            em.add_alternative(body_html, subtype="html")
        try:
            smtp = self._connect_smtp()
            smtp.send_message(em)
        except Exception as exc:  # noqa: BLE001
            raise EmailTransportError(f"SMTP send failed: {exc!r}") from exc
        return {"ok": True, "transport": self.name, "to": to}

    def close(self) -> None:
        for handle, closer in ((self._imap, "logout"), (self._smtp, "quit")):
            if handle is not None:
                try:
                    getattr(handle, closer)()
                except Exception:  # noqa: BLE001
                    pass
        self._imap = None
        self._smtp = None


# ---------------------------------------------------------------- factory ----
def _lookup(source, key: str, default: str = "") -> str:
    if source is None:
        return os.environ.get(key, default)
    if hasattr(source, "get"):
        val = source.get(key)
        return default if val is None else val
    return getattr(source, key, default)


def select_transport(source=None) -> EmailTransport:
    """Choose an email transport.

    Reads ``FABLE_EMAIL_TRANSPORT`` (``emulator`` default, or ``imap``) from
    ``source`` — a mapping / config-like object — or the process environment
    when ``source`` is None. Constructing the chosen transport never opens a
    network connection.
    """
    mode = (_lookup(source, "FABLE_EMAIL_TRANSPORT", "emulator") or "emulator").strip().lower()
    if mode in ("imap", "imap_smtp", "smtp"):
        return ImapSmtpTransport(
            imap_host=_lookup(source, "IMAP_HOST", "localhost"),
            imap_port=int(_lookup(source, "IMAP_PORT", "993") or "993"),
            smtp_host=_lookup(source, "SMTP_HOST", "localhost"),
            smtp_port=int(_lookup(source, "SMTP_PORT", "465") or "465"),
            user=_lookup(source, "EMAIL_USER", ""),
            password=_lookup(source, "EMAIL_PASSWORD", ""),
            support_email=_lookup(source, "SUPPORT_EMAIL", "") or config.SUPPORT_EMAIL,
        )
    return MailboxEmulatorTransport()
