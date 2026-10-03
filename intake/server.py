"""Small local-only review server. Not installed in any production service."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie, CookieError
import hmac
import json
import secrets
import socketserver
import sqlite3
from urllib.parse import parse_qs, urlsplit

from . import import_store, delivery, auth, team, assistance
from .policy import CAPABILITIES, Conflict, Invalid, ROOT
from .records import canonical

MAX_REQUEST = 25 * 1024 * 1024
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
       "img-src 'none'; font-src 'none'; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'; form-action 'none'")


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # HTTPServer's default server_bind resolves a hostname. No DNS is needed.
        socketserver.TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.socket.getsockname()[1]

    def __init__(self, port, store, workspace):
        self.store, self.workspace = store, workspace
        self.audience = secrets.token_urlsafe(32)
        self.csrf_secret = secrets.token_bytes(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.allowed_host = f"127.0.0.1:{self.server_port}"
        self.origin = "http://" + self.allowed_host
        self.cookie_name = "intake_session_" + str(self.server_port)


class Handler(BaseHTTPRequestHandler):
    server_version = "IntakeSandbox"

    def log_message(self, *args):
        pass  # Request URLs, queries and customer content must not enter logs.

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def answer(self, status, body, content_type="application/json; charset=utf-8", cookie=None):
        if not isinstance(body, bytes):
            body = canonical(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if cookie is not None:
            self.send_header("Set-Cookie", self.server.cookie_name + "=" + cookie +
                             "; Path=/; HttpOnly; SameSite=Strict; Max-Age=" + (str(auth.SESSION_SECONDS) if cookie else "0"))
        self.end_headers()
        self.wfile.write(body)

    def trusted(self):
        if (self.headers.get("Host") != self.server.allowed_host or
            self.headers.get("Sec-Fetch-Site") == "cross-site" or
            self.headers.get("Origin", self.server.origin) != self.server.origin):
            self.answer(403, {"error": "Only the local sandbox origin is allowed."})
            return False
        return True

    def session(self, csrf=True):
        try:
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            raw = cookies[self.server.cookie_name].value if self.server.cookie_name in cookies else ''
        except CookieError:
            raw = ''
        identity = auth.identity(self.server.store, raw, self.server.audience)
        token = hmac.new(self.server.csrf_secret, raw.encode(), 'sha256').hexdigest()
        if csrf and not secrets.compare_digest(self.headers.get('X-Intake-Token', ''), token):
            raise auth.Denied('Refresh this page to establish its session protection.')
        return identity, token

    def do_GET(self):
        if not self.trusted():
            return
        parsed = urlsplit(self.path)
        static = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/delivery.js": ("delivery.js", "text/javascript; charset=utf-8"),
                  "/styles.css": ("styles.css", "text/css; charset=utf-8"),
                  "/auth.js": ("auth.js", "text/javascript; charset=utf-8"),
                  "/assistance.js": ("assistance.js", "text/javascript; charset=utf-8"),
                  "/login": ("login.html", "text/html; charset=utf-8"),
                  "/login.js": ("login.js", "text/javascript; charset=utf-8")}
        inbox_static = {'/inbox/': ('index.html', 'text/html; charset=utf-8'),
                        '/inbox/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                        '/inbox/client.js': ('client.js', 'text/javascript; charset=utf-8'),
                        '/inbox/icons.js': ('icons.js', 'text/javascript; charset=utf-8'),
                        '/inbox/styles.css': ('styles.css', 'text/css; charset=utf-8'),
                        '/inbox/offline.css': ('offline.css', 'text/css; charset=utf-8')}
        if parsed.path in inbox_static:
            filename, mime = inbox_static[parsed.path]
            return self.answer(200, (ROOT / 'inbox' / filename).read_bytes(), mime)
        if parsed.path in static:
            filename, mime = static[parsed.path]
            return self.answer(200, (ROOT / "static" / filename).read_bytes(), mime)
        try:
            identity, token = self.session(csrf=parsed.path != '/api/session')
            with auth.acting(identity):
                if parsed.path == '/api/session':
                    value = {'token': token, 'workspace': self.server.workspace, 'capabilities': CAPABILITIES,
                             'user': {k: identity[k] for k in ('id','username','name','role','active')},
                             'expires_at': identity['expires_at'], 'permissions': sorted(auth.ROLES[identity['role']])}
                elif parsed.path == '/api/team':
                    value = team.members(self.server.store)
                elif parsed.path == "/api/tickets":
                    qs = parse_qs(parsed.query)
                    value = self.server.store.list_tickets(qs.get("query", [""])[0], qs.get("status", [""])[0],
                        int(qs.get("offset", [0])[0]), qs.get("queue", ["work"])[0], qs.get('view', ['all'])[0])
                elif parsed.path.startswith("/api/tickets/") and len(parsed.path.split("/")) == 4:
                    value = self.server.store.get_ticket(parsed.path.split("/")[-1])
                elif parsed.path.startswith('/api/tickets/') and parsed.path.endswith('/assistance-input') and len(parsed.path.split('/')) == 5:
                    value = assistance.read_input(self.server.store,parsed.path.split('/')[3])
                elif parsed.path == "/api/imports":
                    with auth.acting(identity, 'import'):
                        value = self.server.store.import_history()
                else:
                    return self.answer(404, {"error": "Route unavailable in the offline sandbox."})
            self.answer(200, value)
        except auth.Denied as exc:
            self.answer(exc.status, {'error': str(exc)})
        except (Invalid, ValueError):
            self.answer(400, {"error": "Invalid request or ticket not found."})
        except sqlite3.Error:
            self.answer(503, {"error": "Sandbox storage is unavailable. No external operation was attempted."})

    def do_POST(self):
        if not self.trusted():
            return
        path = urlsplit(self.path).path
        # An explicit deny also covers attempts to reuse production paths/toggles.
        if any(word in path.lower() for word in ("send", "webhook", "pull", "sync", "rewrite", "notify", "activate")):
            return self.answer(403, {"error": "Live intake, delivery and AI are disabled in this sandbox."})
        try:
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise Invalid("Use application/json.")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_REQUEST or self.headers.get("Transfer-Encoding"):
                raise Invalid("Request must have a bounded Content-Length of at most 25 MiB.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise Invalid("Expected a JSON object.")
            store = self.server.store
            if path == '/api/auth/login':
                if set(data) != {'username', 'password'}:
                    raise Invalid('Provide only a sandbox username and password.')
                raw = auth.login(store, data['username'], data['password'], self.server.audience)
                return self.answer(200, {'signed_in': True}, cookie=raw)
            identity, _ = self.session()
            permission = ('import' if path.startswith('/api/import/') else 'read' if
                          path in ('/api/auth/logout', '/api/auth/password') or path.endswith('/read-state') else 'work')
            with auth.acting(identity, permission):
                # Recheck even routes that do not reach a storage transaction.
                with store.connection():
                    pass
                parts = path.split('/')
                if path == '/api/auth/logout':
                    auth.logout(store)
                    return self.answer(200, {'signed_out': True}, cookie='')
                if path == '/api/auth/password':
                    result = auth.change_password(store, data)
                    return self.answer(200, result, cookie='')
                if path == "/api/tickets":
                    result = store.create_ticket(data)
                elif len(parts) == 5 and parts[1:3] == ["api", "tickets"] and parts[4] in {'update', 'notes', 'claim', 'read-state'}:
                    action = {'update': store.update_ticket, 'notes': store.add_note,
                              'claim': lambda tid, value: team.claim(store, tid, value),
                              'read-state': lambda tid, value: team.mark(store, tid, value)}[parts[4]]
                    result = action(parts[3], data)
                elif len(parts) == 5 and parts[1:3] == ["api", "tickets"] and parts[4] in {
                        'simulation-review', 'simulation-confirm', 'simulation-reconcile'}:
                    action = {'simulation-review': delivery.review, 'simulation-confirm': delivery.confirm,
                              'simulation-reconcile': delivery.reconcile}[parts[4]]
                    result = action(store, parts[3], data)
                elif len(parts) == 5 and parts[1:3] == ['api','tickets'] and parts[4] in {'assistance-run','assistance-use','assistance-dismiss'}:
                    action = {'assistance-run':assistance.run,'assistance-use':assistance.use,'assistance-dismiss':assistance.dismiss}[parts[4]]
                    result = action(store,parts[3],data)
                elif path in {"/api/import/preview", "/api/import/commit"}:
                    if not isinstance(data.get("export_text"), str):
                        raise Invalid("Provide the original export file as export_text.")
                    payload = data["export_text"].encode()
                    result = (import_store.preview(store, payload, data.get("account")) if path.endswith("preview") else
                              import_store.apply_import(store, payload, data.get("account"), data.get("expected_digest")))
                else:
                    return self.answer(404, {"error": "Route unavailable in the offline sandbox."})
            self.answer(200, result)
        except auth.Denied as exc:
            self.answer(exc.status, {'error': str(exc)})
        except Conflict as exc:
            self.answer(409, {"error": str(exc)})
        except Invalid as exc:
            self.answer(400, {"error": str(exc)})
        except (ValueError, UnicodeError, TypeError, RecursionError):
            self.answer(400, {"error": "Invalid request JSON or field type."})
        except (sqlite3.Error, OSError):
            self.answer(503, {"error": "Sandbox storage is unavailable. Refresh and inspect any simulation record before another attempt."})


def serve(store, workspace, port):
    server = LocalServer(port, store, workspace)
    print(f"Offline intake: {server.origin}/ — workspace {workspace}", flush=True)
    print("Live intake, delivery, AI and provider access are disabled.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
