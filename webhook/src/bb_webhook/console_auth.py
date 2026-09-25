"""Password and signed-session helpers for the human support console."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

try:
    import crypt
except ImportError:  # pragma: no cover - platform-dependent legacy migration path
    crypt = None


_PASSWORD_SCHEME = "pbkdf2_sha256"
_PASSWORD_ITERATIONS = 600_000
_SESSION_TTL = timedelta(hours=12)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """Return a self-contained PBKDF2 password hash for environment storage."""

    if not password:
        raise ValueError("password must not be empty")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PASSWORD_ITERATIONS,
    )
    return "$".join(
        (_PASSWORD_SCHEME, str(_PASSWORD_ITERATIONS), _b64encode(salt), _b64encode(digest))
    )


def verify_password(password: str, encoded: str) -> bool:
    """Verify a password without revealing whether the hash is malformed."""

    # The production proxy still owns a bcrypt verifier from the former Basic
    # Auth setup. Accept only its bounded bcrypt form during the migration so
    # the app can be switched to a standalone login without handling plaintext
    # credentials during deployment. New values should use PBKDF2 via
    # ``hash_password``.
    if encoded.startswith(("$2a$", "$2b$", "$2y$")):
        if crypt is None or len(encoded) != 60:
            return False
        try:
            actual = crypt.crypt(password, encoded)
            return bool(actual) and hmac.compare_digest(actual, encoded)
        except (TypeError, ValueError, UnicodeError):
            return False

    try:
        scheme, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        iterations = int(iterations_text)
        if scheme != _PASSWORD_SCHEME or not 100_000 <= iterations <= 2_000_000:
            return False
        salt = _b64decode(salt_text)
        expected = _b64decode(digest_text)
        if len(salt) < 16 or len(expected) != hashlib.sha256().digest_size:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        )
        return hmac.compare_digest(actual, expected)
    except (binascii.Error, TypeError, ValueError, UnicodeError):
        return False


def _timestamp(now: datetime | None) -> int:
    current = now or datetime.now(timezone.utc)
    return int(current.timestamp())


def build_session_token(
    username: str,
    secret: str,
    *,
    now: datetime | None = None,
) -> str:
    """Create an expiring, tamper-evident session token."""

    if not username or not secret:
        raise ValueError("username and secret are required")
    expires = _timestamp(now) + int(_SESSION_TTL.total_seconds())
    payload = _b64encode(f"v2\n{username}\n{expires}\n{secrets.token_urlsafe(32)}".encode("utf-8"))
    signature = _b64encode(hmac.new(secret.encode("utf-8"), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


@dataclass(frozen=True)
class SessionClaims:
    username: str
    expires_at: int
    token_id: str
    version: int


def session_claims(token: str | None, secret: str, *, now: datetime | None = None) -> SessionClaims | None:
    """Verify canonical signed tokens; registry checks are a separate DB step.

    Canonical encoding matters: alternate base64 spellings must not create new
    registry IDs for an already revoked legacy session.
    """
    if not token or len(token) > 4096 or not secret:
        return None
    try:
        payload, signature = token.split(".", 1)
        decoded = _b64decode(payload)
        signed = _b64decode(signature)
        if _b64encode(decoded) != payload or _b64encode(signed) != signature:
            return None
        expected = hmac.new(secret.encode("utf-8"), payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(signed, expected):
            return None
        parts = decoded.decode("utf-8").split("\n")
        if len(parts) == 2:
            username, expires_text = parts
            version = 1
        elif len(parts) == 4 and parts[0] == "v2" and len(parts[3]) == 43:
            _, username, expires_text, nonce = parts
            version = 2
        else:
            return None
        expires = int(expires_text)
        if not username or _timestamp(now) >= expires:
            return None
        return SessionClaims(username, expires, hashlib.sha256(token.encode()).hexdigest(), version)
    except (binascii.Error, TypeError, ValueError, UnicodeError):
        return None


def verify_session_token(token: str | None, secret: str, *, now: datetime | None = None) -> str | None:
    claims = session_claims(token, secret, now=now)
    return claims.username if claims else None


def safe_next_path(value: str | None) -> str:
    """Allow console/inbox relative destinations without traversal or redirects."""
    if not isinstance(value, str) or len(value) > 2048:
        return "/console/"
    decoded = unquote(value)
    if any(ord(char) < 32 or ord(char) == 127 for char in decoded) or "\\" in decoded:
        return "/console/"
    try:
        parsed = urlsplit(decoded)
    except ValueError:
        return "/console/"
    if parsed.scheme or parsed.netloc or any(part in (".", "..") for part in parsed.path.split("/")):
        return "/console/"
    if parsed.path not in ("/console", "/inbox", "/inbox2") and not parsed.path.startswith(("/console/", "/inbox/", "/inbox2/")):
        return "/console/"
    # Encoded path separators and double encoding can be interpreted differently
    # by a browser/proxy. Return only the decoded, unambiguous relative URL.
    if "%" in decoded or decoded.startswith("//"):
        return "/console/"
    return decoded
