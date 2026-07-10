"""Integration: the email transport interface (Stream R / R3).

Two implementations sit behind one interface:
  * MailboxEmulatorTransport — passes straight through to the local mailbox
    emulator (nothing leaves localhost).
  * ImapSmtpTransport — a real IMAP/SMTP skeleton that opens NO connection until
    it is actually used (constructing one, even with an unroutable host, dials
    nothing).
A factory picks between them from FABLE_EMAIL_TRANSPORT. No sockets are opened
by any test here.
"""
import imaplib
import smtplib

import pytest


@pytest.fixture
def channels(server_modules):
    from app import channels_email
    return channels_email


# --- factory selection ------------------------------------------------------
def test_factory_defaults_to_emulator(channels, monkeypatch):
    monkeypatch.delenv("FABLE_EMAIL_TRANSPORT", raising=False)
    t = channels.select_transport()
    assert isinstance(t, channels.MailboxEmulatorTransport)
    assert t.name == "emulator"


def test_factory_selects_imap_from_env(channels, monkeypatch):
    monkeypatch.setenv("FABLE_EMAIL_TRANSPORT", "imap")
    t = channels.select_transport()
    assert isinstance(t, channels.ImapSmtpTransport)
    assert t.name == "imap"


def test_factory_selects_from_mapping(channels):
    cfg = {"FABLE_EMAIL_TRANSPORT": "imap", "IMAP_HOST": "imap.example.com",
           "SMTP_HOST": "smtp.example.com"}
    t = channels.select_transport(cfg)
    assert isinstance(t, channels.ImapSmtpTransport)
    assert t.imap_host == "imap.example.com"
    assert t.smtp_host == "smtp.example.com"


# --- ImapSmtpTransport laziness (never connects at construction) ------------
def test_imap_transport_opens_no_connection_at_construction(channels, monkeypatch):
    # Any attempt to open a socket during construction would raise here.
    def _boom(*_a, **_k):
        raise AssertionError("a connection was opened during construction!")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", _boom)
    monkeypatch.setattr(imaplib, "IMAP4", _boom)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _boom)
    monkeypatch.setattr(smtplib, "SMTP", _boom)

    # 10.255.255.1 is an unroutable host; because construction is lazy, it is
    # never dialed and no exception is raised.
    t = channels.ImapSmtpTransport(
        imap_host="10.255.255.1", imap_port=993,
        smtp_host="10.255.255.1", smtp_port=465,
        user="who@example.com", password="secret",
    )
    # No connection objects exist yet.
    assert t._imap is None
    assert t._smtp is None
    # config was retained for later, lazy use
    assert t.imap_host == "10.255.255.1"
    assert t.support_email == "who@example.com"


def test_factory_imap_construction_is_also_lazy(channels, monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("connection opened via factory construction!")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", _boom)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _boom)
    monkeypatch.setenv("FABLE_EMAIL_TRANSPORT", "imap")
    monkeypatch.setenv("IMAP_HOST", "10.255.255.1")
    monkeypatch.setenv("SMTP_HOST", "10.255.255.1")

    t = channels.select_transport()  # must not connect
    assert t._imap is None and t._smtp is None


# --- MailboxEmulatorTransport passthrough (in-process) ----------------------
def test_mailbox_transport_fetch_new_is_empty(channels):
    t = channels.MailboxEmulatorTransport(base_url="")
    assert t.fetch_new() == []


def test_mailbox_transport_sends_through_emulator(env, channels):
    # Inject the in-process mailbox TestClient's .post so no socket is used.
    t = channels.MailboxEmulatorTransport(base_url="", http_post=env.mailbox.post)

    assert env.mailbox.get("/outbox").json()["count"] == 0
    result = t.send(to="parent@example.com", subject="Your order",
                    body_text="It shipped!")
    assert result["ok"] is True
    assert result["transport"] == "emulator"

    outbox = env.mailbox.get("/outbox").json()
    assert outbox["count"] == 1
    assert outbox["outbox"][0]["to"] == "parent@example.com"
    assert outbox["outbox"][0]["body_text"] == "It shipped!"


def test_mailbox_transport_raises_on_transport_failure(channels):
    def _fail(*_a, **_k):
        raise ConnectionError("mailbox down")

    t = channels.MailboxEmulatorTransport(base_url="http://127.0.0.1:9603",
                                          http_post=_fail)
    with pytest.raises(channels.EmailTransportError):
        t.send(to="x@example.com", subject="s", body_text="b")


def test_transports_satisfy_the_interface(channels):
    emu = channels.MailboxEmulatorTransport(base_url="")
    imap = channels.ImapSmtpTransport(imap_host="h", smtp_host="h")
    for t in (emu, imap):
        assert hasattr(t, "fetch_new")
        assert hasattr(t, "send")
        assert isinstance(t.name, str)
