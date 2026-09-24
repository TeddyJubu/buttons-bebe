#!/usr/bin/env python3
"""
Gorgias API key encryption/decryption utility.

Uses Fernet symmetric encryption (from the cryptography library).
The key is derived from a machine-specific secret stored in /etc/gorgias-wh-key
(encrypted file, readable only by root).

Usage:
  python3 crypto_util.py encrypt <plaintext>   -> prints encrypted token
  python3 crypto_util.py decrypt <ciphertext>   -> prints plaintext
  python3 crypto_util.py setup                  -> generate machine key (run once)
  python3 crypto_util.py status                 -> check if keys exist
"""

import os
import sys
import secrets
import hashlib
import base64

KEY_FILE = "/etc/gorgias-wh-key"
CONFIG_PATH = "/root/gorgias-webhook/config.json"


def _ensure_key():
    """Get or create the machine encryption key."""
    if not os.path.exists(KEY_FILE):
        print(f"Key file not found at {KEY_FILE}. Run: python3 {sys.argv[0]} setup")
        sys.exit(1)
    with open(KEY_FILE, "rb") as f:
        key = f.read().strip()
    if not key:
        raise RuntimeError(
            f"Key file {KEY_FILE} is empty or corrupted. Re-run: python3 {sys.argv[0]} setup"
        )
    try:
        import base64 as _b64
        decoded = _b64.b64decode(key)
        if len(decoded) != 32:
            raise ValueError(f"expected 32 bytes, got {len(decoded)}")
    except Exception as exc:
        raise RuntimeError(
            f"Key file {KEY_FILE} is invalid ({exc}). Re-run: python3 {sys.argv[0]} setup"
        ) from exc
    return key


def _fernet_cipher():
    """Create a Fernet cipher using the cryptography library."""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        print("ERROR: cryptography library not installed.")
        print("Install with: pip3 install cryptography  (or: apt install python3-cryptography)")
        sys.exit(1)
    key = _ensure_key()
    # Derive a 32-byte Fernet key from the machine key
    derived = base64.urlsafe_b64encode(hashlib.sha256(key).digest())
    return Fernet(derived)


def setup():
    """Generate the machine encryption key."""
    if os.path.exists(KEY_FILE):
        print(f"Key already exists at {KEY_FILE}. Back it up first if you want to regenerate.")
        resp = input("Overwrite? (type YES to confirm): ")
        if resp != "YES":
            print("Aborted.")
            return

    # Generate a random 256-bit key
    raw_key = secrets.token_bytes(32)
    # Encode for storage
    stored_key = base64.b64encode(raw_key)

    # Write atomically: write to a temp file beside KEY_FILE, then rename.
    tmp_path = KEY_FILE + ".tmp"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, stored_key + b"\n")
    finally:
        os.close(fd)
    os.rename(tmp_path, KEY_FILE)

    print(f"Machine key generated at {KEY_FILE}")
    print(f"Permissions: 600 (root only)")
    print(f"\nNow encrypt your API key:")
    print(f"  python3 {sys.argv[0]} encrypt 'your-api-key-here'")


def encrypt(plaintext):
    """Encrypt a plaintext string."""
    cipher = _fernet_cipher()
    encrypted = cipher.encrypt(plaintext.encode())
    print(encrypted.decode())


def decrypt(ciphertext):
    """Decrypt an encrypted string."""
    cipher = _fernet_cipher()
    decrypted = cipher.decrypt(ciphertext.encode())
    print(decrypted.decode())


def status():
    """Check setup status."""
    print(f"Key file: {KEY_FILE}")
    if os.path.exists(KEY_FILE):
        st = os.stat(KEY_FILE)
        print(f"  exists: yes")
        print(f"  permissions: {oct(st.st_mode)[-3:]}")
        print(f"  size: {st.st_size} bytes")
    else:
        print(f"  exists: no (run setup)")

    # Check if cryptography is available
    try:
        import cryptography
        print(f"\ncryptography library: installed ({cryptography.__version__})")
    except ImportError:
        print(f"\ncryptography library: NOT installed")
        print(f"  Install with: pip3 install cryptography  (or: apt install python3-cryptography)")

    # Check config
    if os.path.exists(CONFIG_PATH):
        import json
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        api_key = cfg.get("gorgias_api_key", "")
        if api_key.startswith("enc:"):
            print(f"\nConfig API key: encrypted ({api_key[:20]}...)")
        elif api_key:
            print(f"\nConfig API key: PLAINTEXT (should be encrypted)")
        else:
            print(f"\nConfig API key: not set")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "setup":
        setup()
    elif cmd == "encrypt":
        import getpass
        plaintext = getpass.getpass("Enter value to encrypt (input hidden): ")
        encrypt(plaintext)
    elif cmd == "decrypt":
        if len(sys.argv) < 3:
            print("Usage: python3 crypto_util.py decrypt <ciphertext>")
            sys.exit(1)
        decrypt(sys.argv[2])
    elif cmd == "status":
        status()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()