#!/usr/bin/env python3
"""
KB Markdown Editor — a tiny, dependency-free web editor for the Buttons Bebe
knowledge base (the markdown 'memory' the Hermes agent reads).

- Lists / reads / writes ONLY *.md files under KB_ROOT (path-traversal safe).
- On save: writes the file, git-commits it, and runs the ingestion sync so the
  agent picks up the change ("save = live"). Indexing runs in the background so
  saving stays instant; /api/status reports publish progress.
- No auth here on purpose: it binds to localhost only and sits behind Caddy
  basic-auth + HTTPS.

Stdlib only. Python 3.
"""
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

KB_ROOT   = os.path.realpath(os.environ.get("KB_EDITOR_KB_ROOT", "/root/gorgias-webhook/kb"))
REPO_ROOT = os.environ.get("KB_EDITOR_REPO_ROOT", "/root/gorgias-webhook")
VENV_PY   = os.environ.get("KB_EDITOR_VENV_PY", "/root/gorgias-webhook/.venv/bin/python")
PORT      = int(os.environ.get("KB_EDITOR_PORT", "8090"))
HERE      = os.path.dirname(os.path.abspath(__file__))

_publish_lock = threading.Lock()
_status = {"state": "idle", "file": None, "ts": 0, "message": "ready"}


def _set_status(**kw):
    _status.update(kw)
    _status["ts"] = time.time()


def _safe_abs(rel):
    """Resolve a kb-relative path to an absolute path INSIDE KB_ROOT, or None."""
    if not rel:
        return None
    rel = unquote(rel).lstrip("/")
    if not rel.endswith(".md") or "\\x00" in rel:
        return None
    ap = os.path.realpath(os.path.join(KB_ROOT, rel))
    if ap != KB_ROOT and not ap.startswith(KB_ROOT + os.sep):
        return None
    return ap


def list_tree():
    items = []
    for dirpath, dirnames, filenames in os.walk(KB_ROOT):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if fn.endswith(".md"):
                items.append(os.path.relpath(os.path.join(dirpath, fn), KB_ROOT))
    items.sort()
    return items


def git_commit(abs_path, rel):
    try:
        subprocess.run(["git", "-C", REPO_ROOT, "add", abs_path],
                       check=True, capture_output=True, timeout=30)
        subprocess.run(
            ["git", "-C", REPO_ROOT, "commit", "-m", "KB edit: " + rel + " (via editor)",
             "--author", "Chaim via KB Editor <chaim@buttonsbebe.local>"],
            capture_output=True, text=True, timeout=30)
        return True
    except Exception:
        return False


def publish_async(abs_path, rel):
    def run():
        with _publish_lock:
            _set_status(state="running", file=rel, message="publishing to agent...")
            git_commit(abs_path, rel)
            try:
                def _ingest(mode):
                    return subprocess.run([VENV_PY, "ingestion_worker.py", mode],
                                          cwd=REPO_ROOT, capture_output=True, text=True, timeout=600)
                p = _ingest("sync")
                if p.returncode != 0:
                    # self-heal (e.g. stale index pointer): rebuild from scratch
                    _set_status(state="running", file=rel, message="re-indexing (repair)...")
                    p = _ingest("full")
                if p.returncode == 0:
                    _set_status(state="done", file=rel, message="published to agent")
                else:
                    tail = (p.stderr or p.stdout or "")[-300:]
                    _set_status(state="error", file=rel, message="saved, but indexing failed: " + tail)
            except Exception as e:
                _set_status(state="error", file=rel, message="saved, but indexing error: " + str(e))
    threading.Thread(target=run, daemon=True).start()


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except Exception:
                self._send(500, {"error": "index.html missing"})
            return
        if u.path.startswith("/vendor/"):
            fn = u.path[len("/vendor/"):]
            ap = os.path.join(HERE, "vendor", fn)
            if ("/" in fn) or (".." in fn) or (not fn) or (not os.path.isfile(ap)):
                self._send(404, {"error": "not found"})
                return
            ct = "application/javascript" if fn.endswith(".js") else "text/css" if fn.endswith(".css") else "application/octet-stream"
            with open(ap, "rb") as f:
                self._send(200, f.read(), ct)
            return
        if u.path == "/api/tree":
            self._send(200, {"files": list_tree()})
            return
        if u.path == "/api/status":
            self._send(200, _status)
            return
        if u.path == "/api/file":
            rel = (parse_qs(u.query).get("path") or [""])[0]
            ap = _safe_abs(rel)
            if not ap or not os.path.isfile(ap):
                self._send(404, {"error": "not found"})
                return
            with open(ap, encoding="utf-8") as f:
                self._send(200, {"path": unquote(rel).lstrip("/"), "content": f.read()})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/api/file":
            self._send(404, {"error": "not found"})
            return
        try:
            ln = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(ln) or b"{}")
        except Exception:
            self._send(400, {"error": "bad json"})
            return
        rel = (data.get("path") or "").strip()
        content = data.get("content")
        ap = _safe_abs(rel)
        if not ap or content is None:
            self._send(400, {"error": "invalid path or content"})
            return
        publish = bool(data.get("publish", True))
        try:
            os.makedirs(os.path.dirname(ap), exist_ok=True)
            with open(ap, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            self._send(500, {"error": "write failed: " + str(e)})
            return
        if publish:
            publish_async(ap, rel.lstrip("/"))
            self._send(200, {"ok": True, "published": True, "path": rel.lstrip("/"), "message": "saved; publishing to agent..."})
        else:
            self._send(200, {"ok": True, "published": False, "path": rel.lstrip("/"), "message": "saved"})


if __name__ == "__main__":
    print("KB editor on 127.0.0.1:" + str(PORT) + "  KB_ROOT=" + KB_ROOT)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
