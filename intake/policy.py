"""Non-configurable offline boundary for the sandbox entry point."""
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
CAPABILITIES = {
    "mode": "offline_sandbox", "liveIntake": False, "sendReply": False,
    "providerReads": False, "aiGeneration": False, "notifications": False,
    "manualTickets": True, "exportImport": True, "internalNotes": True,
    "fakeDelivery": True, "assistanceFixtures": True,
}


class Invalid(ValueError):
    pass


class Conflict(Invalid):
    pass


def install_offline_guard():
    """Block outbound sockets/DNS and child processes, including localhost APIs.

    A Python audit hook is defense in depth, not an OS security sandbox against
    hostile code. The service contains no providers, plugins or credential loader.
    Accepted HTTP connections can respond; connect/sendto calls cannot originate
    traffic. Install before opening the database or starting the HTTP server.
    """
    blocked = {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname",
               "socket.gethostbyaddr", "socket.sendto", "socket.sendmsg",
               "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn"}

    def guard(event, args):
        if event in blocked:
            raise PermissionError("Offline intake: external operations are disabled.")

    sys.addaudithook(guard)


def workspace_directory(name):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", name):
        raise Invalid("Workspace must use 1–48 lowercase letters, digits or hyphens.")
    base = ROOT / ".local"
    directory = base / name
    for path in (base, directory):
        if path.is_symlink():
            raise Invalid("Sandbox directories cannot be symbolic links.")
        path.mkdir(mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
    return directory
