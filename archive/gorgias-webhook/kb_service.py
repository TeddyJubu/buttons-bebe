#!/usr/bin/env python3
"""
kb_service.py — THE KB BLACK BOX (a standalone localhost-only HTTP service).

Stage 4+ (Buttons Bebe AI support agent). This is the self-contained semantic
retrieval service. It is a true black box: input = a question, output = ranked
answers. It runs in its OWN process, so it CANNOT block or crash the webhook,
and CANNOT be blocked or crashed by the webhook.

WHAT IT OWNS (the heavy parts that USED to live in-process inside kb_client):
  * the embeddings model (embeddings.embed_one — fastembed, 384-dim) — loaded
    ONCE, lazily, on the first /search that needs it;
  * the pgvector connection (ingestion_worker.get_conn / DSN);
  * the semantic-search logic itself (the cosine top-k query, MOVED here from
    kb_client._search_pgvector).

Because the webhook talks to this over HTTP (via the thin kb_client), importing
kb_client no longer pulls in fastembed or pg8000 — the webhook process stays
pure stdlib. The ONLY process that imports those heavy deps is THIS one.

ENDPOINTS (all JSON; bound to 127.0.0.1 ONLY — never public):
  GET  /health
        -> 200 {"status":"ok"|"degraded","backend":"pgvector","rows":N,
                "model":..., "detail":...}. NEVER throws; if the DB or model is
        not ready it reports "degraded" with 200 so a health probe still works.
  POST /search   body {"question": str, "top_k": int, "min_score": float|null}
        -> 200 {"results":[{source,title,category,heading,text,score,status,
                tags}], "backend":"pgvector", "took_ms":N}.
        A genuine no-match returns {"results":[]} with 200 — that is a REAL KB
        gap, NOT an error, and the caller must NOT fall back on it.
        An INTERNAL failure (DB down, embed error) returns a clear non-2xx (503)
        with {"error":...} so the CALLER knows to fall back to its local file
        backend. The service itself NEVER crashes: it catches everything, logs,
        and keeps serving.
  POST /reload   -> reset caches / re-open DB -> {"ok":true}.
  POST /ingest   -> call ingestion_worker.sync() and return its stats, so the
        write-back path can refresh the index WITHOUT the webhook ever touching
        the DB. (Optional; returns 503 on failure, never crashes.)

HARDENING: every handler is wrapped so a bad/garbage request, an over-size body,
weird unicode, or a DB hiccup can never take the server down. There is a request
body-size cap (MAX_BODY_BYTES) and the server is multi-threaded
(ThreadingHTTPServer) so one slow query can't block a /health probe or a second
search.

Run it (reads host/port from config.json kb_service / env):
    .venv/bin/python kb_service.py

Needs .venv (fastembed + pg8000) — it is the SERVICE that owns those deps.
"""

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("kb_service")

# --------------------------------------------------------------------------- #
# Config / paths
# --------------------------------------------------------------------------- #
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

# Cosine relevance floor for the pgvector backend (0..1). Identical default to
# the value kb_client used in-process, so the served results are unchanged.
PGVECTOR_MIN_SCORE = 0.62

# Hard cap on a /search (or any) request body. A "question" is a short string;
# anything past this is garbage/abuse and is rejected with 413 rather than read.
MAX_BODY_BYTES = 64 * 1024  # 64 KiB

# The cosine top-k query. similarity = 1 - (embedding <=> query); ordering by the
# `<=>` distance asc == similarity desc and lets the HNSW index do the work.
_PGVECTOR_SQL = (
    "SELECT source, title, category, heading, status, tags, chunk_text, "
    "       1 - (embedding <=> CAST(:q AS vector)) AS similarity "
    "FROM kb_chunks "
    "ORDER BY embedding <=> CAST(:q AS vector) "
    "LIMIT :k"
)


def _load_service_config():
    """Read the kb_service block from config.json (host/port), with env override.

    Returns (host, port). host is ALWAYS forced to a loopback address — this
    service must never bind publicly, regardless of config. Defaults: 127.0.0.1
    :8899.
    """
    host = "127.0.0.1"
    port = 8899
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = json.load(fh).get("kb_service") or {}
        if cfg.get("host"):
            host = str(cfg["host"]).strip()
        if cfg.get("port"):
            port = int(cfg["port"])
        # A url like "http://127.0.0.1:8899" can also carry the port.
        url = cfg.get("url")
        if url and not cfg.get("port"):
            from urllib.parse import urlparse

            u = urlparse(url)
            if u.hostname:
                host = u.hostname
            if u.port:
                port = u.port
    except (OSError, ValueError, TypeError):
        pass

    host = os.environ.get("KB_SERVICE_HOST", host)
    try:
        port = int(os.environ.get("KB_SERVICE_PORT", port))
    except (TypeError, ValueError):
        pass

    # SAFETY: never bind to a public interface. Force loopback unless it already
    # is one. (We only ever want this reachable from the same host.)
    if host not in ("127.0.0.1", "localhost", "::1"):
        log.warning(
            "kb_service host %r is not loopback — forcing 127.0.0.1 (localhost-only).",
            host,
        )
        host = "127.0.0.1"
    return host, port


# --------------------------------------------------------------------------- #
# The engine — owns the model + DB. Lazy, cached, reload-able, thread-safe.
# --------------------------------------------------------------------------- #
class _Engine:
    """Holds the semantic-search dependencies (embeddings model + pgvector).

    All heavy imports (embeddings/fastembed, ingestion_worker/pg8000) happen
    INSIDE this class, never at module import, so even importing kb_service is
    cheap until the first search. Every public method is hardened to translate
    infra failures into a clear signal for the HTTP layer (it raises a plain
    Exception that the handler turns into a 503), and NEVER lets the process die.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # We deliberately do NOT pin a long-lived connection; pgvector queries
        # open + close a short-lived conn per search (mirrors the old in-process
        # path) so a dropped DB connection self-heals on the next request.

    # -- embeddings -------------------------------------------------------- #
    def model_name(self):
        try:
            import embeddings

            return embeddings.MODEL_NAME
        except Exception:
            return None

    def warm_model(self):
        """Force the embeddings model to load now (used by /reload). Best-effort."""
        import embeddings

        embeddings.embed_one("warm up")
        return embeddings.MODEL_NAME

    # -- search ------------------------------------------------------------ #
    def search(self, question, top_k, min_score):
        """Semantic cosine search over kb_chunks. Returns list[dict] best-first.

        `min_score` is on the cosine scale (0..1). Chunks below it are dropped,
        so an unrelated query returns [] (a genuine KB gap, returned NORMALLY,
        not as an error). RAISES on infra failure (no DB / no embeddings model /
        query error) — the HTTP layer turns that into a 503 so the caller falls
        back. Imports the heavy deps lazily.
        """
        import ingestion_worker
        from embeddings import embed_one, to_pgvector_literal

        qvec = embed_one(question)
        if not qvec:  # empty embedding -> nothing to compare; genuine gap.
            return []
        q_literal = to_pgvector_literal(qvec)

        conn = ingestion_worker.get_conn()
        try:
            rows = conn.run(_PGVECTOR_SQL, q=q_literal, k=int(top_k))
        finally:
            try:
                conn.close()
            except Exception:
                pass

        hits = []
        for source, title, category, heading, status, tags_json, chunk_text, sim in rows:
            similarity = float(sim)
            if similarity < min_score:
                continue  # below the cosine floor -> not a confident match
            try:
                tags = json.loads(tags_json) if tags_json else []
            except (TypeError, ValueError):
                tags = []
            hits.append(
                {
                    "source": source,
                    "title": title or "",
                    "category": category or "",
                    "heading": heading,
                    "text": chunk_text or "",
                    "score": round(similarity, 4),
                    "status": status or "",
                    "tags": list(tags) if isinstance(tags, list) else [],
                }
            )
        return hits[:top_k]

    # -- health ------------------------------------------------------------ #
    def row_count(self):
        """Best-effort count of kb_chunks rows. Returns int or None (degraded)."""
        try:
            import ingestion_worker

            conn = ingestion_worker.get_conn()
            try:
                rows = conn.run("SELECT count(*) FROM kb_chunks")
                return int(rows[0][0]) if rows else 0
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception as exc:
            log.warning("health: row_count failed: %s", exc)
            return None

    # -- reload ------------------------------------------------------------ #
    def reload(self):
        """Reset/re-open: model stays loaded (it's immutable), but we re-warm it
        and confirm the DB is reachable. Returns a small status dict. Never
        raises (collects errors into the dict).
        """
        out = {"model": None, "rows": None, "errors": []}
        with self._lock:
            try:
                out["model"] = self.warm_model()
            except Exception as exc:
                out["errors"].append(f"model: {exc}")
            rows = self.row_count()
            out["rows"] = rows
            if rows is None:
                out["errors"].append("db: kb_chunks unreachable")
        return out

    # -- ingest ------------------------------------------------------------ #
    def ingest(self):
        """Run an incremental ingestion_worker.sync(). Returns its stats dict.

        RAISES on failure (the HTTP layer -> 503). Serialized so two concurrent
        /ingest calls don't fight over the index.
        """
        import ingestion_worker

        with self._lock:
            return ingestion_worker.sync()


_ENGINE = _Engine()


# --------------------------------------------------------------------------- #
# HTTP handler — every route wrapped so nothing can crash the process.
# --------------------------------------------------------------------------- #
class _KBHandler(BaseHTTPRequestHandler):
    """Routes:  GET /health ;  POST /search ;  POST /reload ;  POST /ingest .

    The dispatch in do_GET/do_POST is wrapped in a blanket try/except so a
    malformed request, a unicode quirk, or an unexpected engine error becomes a
    logged 500 RESPONSE — never an unhandled exception that could take down the
    request thread in a surprising way. (ThreadingHTTPServer already isolates
    one request from another, but we belt-and-suspenders it.)
    """

    server_version = "kb_service/1.0"
    protocol_version = "HTTP/1.1"

    # -- logging: route through our logger, not stderr --------------------- #
    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    # -- response helpers -------------------------------------------------- #
    def _send_json(self, status, payload):
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            body = b'{"error":"unserializable response"}'
            status = 500
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Caller hung up (e.g. timed out and walked away). Not our problem.
            pass
        except Exception as exc:  # never propagate out of the response write
            log.warning("failed writing response: %s", exc)

    def _read_body(self):
        """Read the request body honoring Content-Length, with a hard size cap.

        Returns bytes, or raises ValueError("too large") if the declared length
        exceeds MAX_BODY_BYTES (so we reject without reading the bytes).
        """
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            length = 0
        if length < 0:
            length = 0
        if length > MAX_BODY_BYTES:
            raise ValueError("request body too large")
        if length == 0:
            return b""
        return self.rfile.read(length)

    def _parse_json_body(self):
        """Read + JSON-parse the body. Returns a dict, or raises ValueError."""
        raw = self._read_body()
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"invalid JSON body: {exc}")
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    # -- GET --------------------------------------------------------------- #
    def do_GET(self):
        try:
            if self.path == "/health" or self.path.startswith("/health?"):
                return self._handle_health()
            return self._send_json(404, {"error": "not found", "path": self.path})
        except Exception as exc:  # absolute backstop
            log.exception("unhandled GET error on %s: %s", self.path, exc)
            return self._send_json(500, {"error": "internal error"})

    # -- POST -------------------------------------------------------------- #
    def do_POST(self):
        try:
            path = self.path.split("?", 1)[0]
            if path == "/search":
                return self._handle_search()
            if path == "/reload":
                return self._handle_reload()
            if path == "/ingest":
                return self._handle_ingest()
            # Drain any body so the connection can be reused cleanly.
            try:
                self._read_body()
            except Exception:
                pass
            return self._send_json(404, {"error": "not found", "path": self.path})
        except ValueError as exc:
            # Bad/garbage/oversize request -> 400/413, service stays up.
            msg = str(exc)
            status = 413 if "too large" in msg else 400
            return self._send_json(status, {"error": msg})
        except Exception as exc:  # absolute backstop
            log.exception("unhandled POST error on %s: %s", self.path, exc)
            return self._send_json(500, {"error": "internal error"})

    # -- handlers ---------------------------------------------------------- #
    def _handle_health(self):
        """NEVER throws. Reports degraded (still 200) if model/DB not ready."""
        rows = _ENGINE.row_count()
        model = _ENGINE.model_name()
        status = "ok" if rows is not None else "degraded"
        detail = "" if rows is not None else "kb_chunks DB unreachable"
        return self._send_json(
            200,
            {
                "status": status,
                "backend": "pgvector",
                "rows": rows,
                "model": model,
                "detail": detail,
            },
        )

    def _handle_search(self):
        """POST /search. Empty result -> 200 {results:[]} (KB gap, NOT error).
        Infra failure -> 503 (caller falls back). Bad request -> 400/413.
        """
        data = self._parse_json_body()  # may raise ValueError -> 400/413
        question = data.get("question")
        if not isinstance(question, str) or not question.strip():
            # No question at all is a client error, not a KB gap.
            return self._send_json(400, {"error": "missing/empty 'question'"})

        top_k = data.get("top_k", 5)
        try:
            top_k = int(top_k)
        except (TypeError, ValueError):
            top_k = 5
        top_k = max(1, min(top_k, 50))  # clamp to something sane

        min_score = data.get("min_score", None)
        if min_score is None:
            min_score = PGVECTOR_MIN_SCORE
        else:
            try:
                min_score = float(min_score)
            except (TypeError, ValueError):
                min_score = PGVECTOR_MIN_SCORE

        t0 = time.monotonic()
        try:
            results = _ENGINE.search(question, top_k, min_score)
        except Exception as exc:
            # Infra failure (DB down / embed error / query error). NON-2xx so the
            # caller falls back — but we caught it, so the service stays alive.
            log.warning("search failed (infra) — returning 503: %s", exc)
            return self._send_json(
                503,
                {"error": "search backend unavailable", "detail": str(exc)},
            )
        took_ms = int((time.monotonic() - t0) * 1000)
        return self._send_json(
            200,
            {"results": results, "backend": "pgvector", "took_ms": took_ms},
        )

    def _handle_reload(self):
        """POST /reload. Always 200 with a status dict (errors collected)."""
        info = _ENGINE.reload()
        return self._send_json(200, {"ok": not info["errors"], **info})

    def _handle_ingest(self):
        """POST /ingest. Refreshes the index via ingestion_worker.sync().

        503 on failure (never crashes the service).
        """
        try:
            stats = _ENGINE.ingest()
        except Exception as exc:
            log.warning("ingest failed — returning 503: %s", exc)
            return self._send_json(503, {"error": "ingest failed", "detail": str(exc)})
        return self._send_json(200, {"ok": True, "stats": stats})


# --------------------------------------------------------------------------- #
# Server bootstrap
# --------------------------------------------------------------------------- #
def make_server(host=None, port=None):
    """Build (but do NOT start) a ThreadingHTTPServer for the KB service.

    Threaded so one slow query never blocks /health or a second /search. host is
    forced to loopback by _load_service_config; passing host/port here overrides
    config/env (used by the test harness to bind an ephemeral test port).
    """
    cfg_host, cfg_port = _load_service_config()
    bind_host = host if host is not None else cfg_host
    bind_port = cfg_port if port is None else port
    # Guard again here in case a caller passed a non-loopback host explicitly.
    if bind_host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("forcing non-loopback host %r to 127.0.0.1", bind_host)
        bind_host = "127.0.0.1"
    httpd = ThreadingHTTPServer((bind_host, bind_port), _KBHandler)
    httpd.daemon_threads = True
    return httpd


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    host, port = _load_service_config()
    httpd = make_server(host, port)
    actual_host, actual_port = httpd.server_address[:2]
    log.info(
        "kb_service listening on http://%s:%s (localhost-only) — backend=pgvector",
        actual_host, actual_port,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("kb_service shutting down (interrupt).")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
