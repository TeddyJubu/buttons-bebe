#!/usr/bin/env python3
"""
test_kb_isolation.py — PROVE the KB black-box isolation guarantees.

Run with the venv (the real kb_service needs fastembed + pg8000):
    .venv/bin/python test_kb_isolation.py

What this proves (stdlib unittest; never touches the live :8899 service or the
real feedback.db, never spams Telegram):

  * SERVICE UP    : the real kb_service on a TEST port returns pgvector (semantic)
                    results through kb_client.search; /health is ok; cosine scores
                    (not BM25) confirm the SERVICE answered.
  * SERVICE DOWN  : point the client at a dead port -> kb_client.search returns the
                    file-BM25 FALLBACK, does NOT raise, and returns well under
                    timeout+1s.
  * SERVICE SLOW  : a stub that sleeps past the timeout -> search times out and
                    falls back within ~timeout; never hangs.
  * 503 / GARBAGE : a stub returning 503, and one returning malformed JSON -> the
                    client falls back, no crash.
  * GENUINE EMPTY : a healthy service that returns {"results":[]} (nonsense query)
                    -> search returns [] (KB gap) and does NOT fall back.
  * ROBUSTNESS    : POST garbage / empty / huge / unicode bodies to the real
                    service /search -> it stays up (answers a later valid request),
                    never 500-crashes the process.
  * CALLER ISOLATION: a slow request to the threaded service does not block a
                    concurrent fast one (ThreadingHTTPServer).
  * DRAFT ENGINE  : draft_engine.generate_draft("where is my order?") works with
                    the service UP (semantic) AND DOWN (file fallback) — same
                    list[KBChunk] contract; draft_engine is NOT edited.

Ends with: KB_ISOLATION TEST OK
"""

import json
import os
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Force the offline mock LLM provider BEFORE importing draft_engine/model_gateway,
# so the draft-engine checks never call a live LLM (and never need a key).
os.environ["LLM_PROVIDER"] = "mock"

import kb_client
import kb_service
import draft_engine


# --------------------------------------------------------------------------- #
# Helpers: spin the REAL service, and tiny stub servers for failure modes.
# --------------------------------------------------------------------------- #
def _free_port():
    """Bind a real kb_service to an ephemeral port; return (httpd, url)."""
    httpd = kb_service.make_server("127.0.0.1", 0)
    port = httpd.server_address[1]
    return httpd, f"http://127.0.0.1:{port}"


def _serve(httpd):
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return t


class _StubHandler(BaseHTTPRequestHandler):
    """A configurable stub: behavior is read from server.mode."""

    def log_message(self, *a):
        pass

    def _drain(self):
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            if n > 0:
                self.rfile.read(n)
        except Exception:
            pass

    def do_POST(self):
        mode = getattr(self.server, "mode", "ok")
        self._drain()
        if mode == "slow":
            # Sleep WELL past the client's timeout, then (try to) answer.
            time.sleep(getattr(self.server, "sleep_for", 5.0))
            self._reply(200, {"results": [], "backend": "pgvector", "took_ms": 9999})
        elif mode == "503":
            self._reply(503, {"error": "backend down"})
        elif mode == "garbage":
            body = b"{not valid json at all <<<"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif mode == "empty":
            self._reply(200, {"results": [], "backend": "pgvector", "took_ms": 1})
        else:
            self._reply(200, {"results": [], "backend": "pgvector", "took_ms": 1})

    def _reply(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _stub(mode, sleep_for=5.0):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    httpd.daemon_threads = True
    httpd.mode = mode
    httpd.sleep_for = sleep_for
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    _serve(httpd)
    return httpd, url


def _point_client(url, timeout):
    """Point kb_client at a service URL/timeout via env (per-call resolution)."""
    os.environ["KB_SERVICE_URL"] = url
    os.environ["KB_SERVICE_TIMEOUT"] = str(timeout)
    os.environ["KB_SERVICE_ENABLED"] = "1"


def _disable_service():
    os.environ["KB_SERVICE_ENABLED"] = "0"


# --------------------------------------------------------------------------- #
# A single REAL service shared across the tests that need a live backend.
# --------------------------------------------------------------------------- #
_REAL_HTTPD = None
_REAL_URL = None


def setUpModule():
    global _REAL_HTTPD, _REAL_URL
    _REAL_HTTPD, _REAL_URL = _free_port()
    _serve(_REAL_HTTPD)
    # Warm the model once up front so per-test timing is about ISOLATION, not the
    # one-time ~model-load. Done via the service itself (POST /reload warms it).
    try:
        req = urllib.request.Request(_REAL_URL + "/reload", data=b"", method="POST")
        urllib.request.urlopen(req, timeout=60).read()
    except Exception as _e:
        import warnings
        warnings.warn(f"setUpModule: /reload warm-up failed: {_e} — "
                      "semantic tests may fail if the model is not loaded.")


def tearDownModule():
    if _REAL_HTTPD is not None:
        _REAL_HTTPD.shutdown()
        _REAL_HTTPD.server_close()


class _EnvRestore(unittest.TestCase):
    """Base: snapshot + restore the KB_SERVICE_* env around each test."""

    def setUp(self):
        self._env = {
            k: os.environ.get(k)
            for k in ("KB_SERVICE_URL", "KB_SERVICE_TIMEOUT", "KB_SERVICE_ENABLED")
        }

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# --------------------------------------------------------------------------- #
# 1. SERVICE UP -> semantic results through the client; /health ok.
# --------------------------------------------------------------------------- #
class TestServiceUp(_EnvRestore):
    def test_health_ok_and_semantic_results(self):
        _point_client(_REAL_URL, 15)

        # /health is ok with the real row count + model.
        health = json.loads(
            urllib.request.urlopen(_REAL_URL + "/health", timeout=15).read()
        )
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["backend"], "pgvector")
        self.assertGreater(health["rows"], 0)
        self.assertTrue(health["model"])

        hits = kb_client.search("where is my order")
        self.assertTrue(hits, "service should return semantic hits for a real query")
        self.assertIsInstance(hits[0], kb_client.KBChunk)
        # Cosine scores live in (0,1]; a BM25 fallback would exceed 1.0. So a
        # top score < 1.0 confirms the SERVICE (pgvector) answered, not the file
        # backend.
        self.assertLessEqual(hits[0].score, 1.0)
        self.assertGreater(hits[0].score, 0.0)


# --------------------------------------------------------------------------- #
# 2. SERVICE DOWN -> file fallback, no raise, fast.
# --------------------------------------------------------------------------- #
class TestServiceDown(_EnvRestore):
    def test_dead_port_falls_back_fast(self):
        # Use a fixed port that is always closed (port 1 is reserved and never
        # open on localhost) to avoid the TOCTOU race of bind-then-close.
        dead_port = 1

        timeout = 3.0
        _point_client(f"http://127.0.0.1:{dead_port}", timeout)

        t0 = time.monotonic()
        hits = kb_client.search("where is my order")
        elapsed = time.monotonic() - t0

        # Falls back to BM25 file backend -> real hits, no exception.
        self.assertTrue(hits, "dead service should fall back to file BM25 results")
        self.assertIsInstance(hits[0], kb_client.KBChunk)
        # Connection-refused is immediate; must be WELL under timeout+1s.
        self.assertLess(elapsed, timeout + 1.0, f"fallback too slow: {elapsed:.2f}s")
        # A BM25 result typically scores > 1.0 (different scale) — sanity that the
        # FILE backend answered, not a stale service result.
        self.assertGreater(hits[0].score, 1.0)


# --------------------------------------------------------------------------- #
# 3. SERVICE SLOW -> times out, falls back within ~timeout, never hangs.
# --------------------------------------------------------------------------- #
class TestServiceSlow(_EnvRestore):
    def test_slow_service_times_out_and_falls_back(self):
        httpd, url = _stub("slow", sleep_for=10.0)
        try:
            timeout = 1.0
            _point_client(url, timeout)
            t0 = time.monotonic()
            hits = kb_client.search("where is my order")
            elapsed = time.monotonic() - t0
            self.assertTrue(hits, "slow service should fall back to file results")
            # Must return ~timeout (not the 10s sleep) — proves the strict cap.
            self.assertLess(elapsed, timeout + 1.5, f"did not honor timeout: {elapsed:.2f}s")
            self.assertGreaterEqual(elapsed, timeout - 0.2)
        finally:
            httpd.shutdown()
            httpd.server_close()


# --------------------------------------------------------------------------- #
# 4. 503 / malformed JSON -> fall back, no crash.
# --------------------------------------------------------------------------- #
class TestServiceErrors(_EnvRestore):
    def test_503_falls_back(self):
        httpd, url = _stub("503")
        try:
            _point_client(url, 3.0)
            hits = kb_client.search("where is my order")
            self.assertTrue(hits, "503 should fall back to file results")
            self.assertGreater(hits[0].score, 1.0)  # BM25 scale -> file backend
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_malformed_json_falls_back(self):
        httpd, url = _stub("garbage")
        try:
            _point_client(url, 3.0)
            hits = kb_client.search("where is my order")
            self.assertTrue(hits, "malformed JSON should fall back to file results")
            self.assertGreater(hits[0].score, 1.0)
        finally:
            httpd.shutdown()
            httpd.server_close()


# --------------------------------------------------------------------------- #
# 5. GENUINE EMPTY from a healthy service -> [] (KB gap), NOT a fallback.
# --------------------------------------------------------------------------- #
class TestGenuineEmpty(_EnvRestore):
    def test_empty_is_kb_gap_not_fallback(self):
        # Use the REAL service with a genuine nonsense query: it returns 200 with
        # an empty result set (below the cosine floor). The client must return []
        # and NOT silently substitute file results (which would mask the gap).
        _point_client(_REAL_URL, 15)
        hits = kb_client.search("purple platypus quantum tax accordion lawnmower zzxq")
        self.assertEqual(hits, [], "healthy empty result must be a KB gap ([]), not a fallback")

    def test_empty_via_stub_does_not_fall_back(self):
        # Belt-and-suspenders with a deterministic stub that ALWAYS returns
        # {"results":[]} for a query that WOULD match the file backend. If the
        # client fell back we'd get file hits; correct behavior is [].
        httpd, url = _stub("empty")
        try:
            _point_client(url, 3.0)
            hits = kb_client.search("where is my order")
            self.assertEqual(hits, [], "200 empty must NOT trigger the file fallback")
        finally:
            httpd.shutdown()
            httpd.server_close()


# --------------------------------------------------------------------------- #
# 6. SERVICE ROBUSTNESS — garbage/empty/huge/unicode bodies don't kill it.
# --------------------------------------------------------------------------- #
class TestServiceRobustness(_EnvRestore):
    def _post(self, path, body_bytes, ctype="application/json"):
        req = urllib.request.Request(
            _REAL_URL + path, data=body_bytes, method="POST",
            headers={"Content-Type": ctype},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_garbage_then_still_serves(self):
        # Garbage (non-JSON) -> 400, not a crash.
        st, _ = self._post("/search", b"\x00\x01 not json \xff\xfe")
        self.assertIn(st, (400, 413))

        # Empty body -> handled (missing 'question' -> 400), not a crash.
        st, _ = self._post("/search", b"")
        self.assertEqual(st, 400)

        # Wrong-type JSON (a list, not an object) -> 400.
        st, _ = self._post("/search", b"[1,2,3]")
        self.assertEqual(st, 400)

        # Weird unicode question -> valid request; 200 (results possibly empty).
        st, _ = self._post(
            "/search",
            json.dumps({"question": "🦄💛 où est ma commande 注文 ๆ"}).encode("utf-8"),
        )
        self.assertEqual(st, 200)

        # HUGE body -> rejected by the size cap (413), NOT read into memory/crash.
        huge = json.dumps({"question": "x" * (kb_service.MAX_BODY_BYTES + 1000)}).encode()
        st, _ = self._post("/search", huge)
        self.assertEqual(st, 413)

        # After ALL of that abuse, a normal request still works -> process alive.
        st, body = self._post(
            "/search", json.dumps({"question": "where is my order"}).encode("utf-8")
        )
        self.assertEqual(st, 200)
        payload = json.loads(body)
        self.assertIn("results", payload)
        self.assertEqual(payload["backend"], "pgvector")

        # /health still ok too.
        h = json.loads(urllib.request.urlopen(_REAL_URL + "/health", timeout=15).read())
        self.assertEqual(h["status"], "ok")


# --------------------------------------------------------------------------- #
# 7. CALLER ISOLATION — a slow request does not block a concurrent fast one.
# --------------------------------------------------------------------------- #
class TestCallerIsolation(_EnvRestore):
    def test_slow_request_does_not_block_fast(self):
        # Fire a /search and a /health concurrently against the SAME real
        # ThreadingHTTPServer. With a proper threaded server the /health must
        # return promptly even while a /search is in-flight on another thread.
        # (The previous version sent the "slow" request to a different stub
        # server, which proved nothing about real-service threading.)
        _point_client(_REAL_URL, 15)
        done = {}

        def _search():
            req = urllib.request.Request(
                _REAL_URL + "/search",
                data=json.dumps({"question": "where is my order"}).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    done["search"] = json.loads(r.read())
            except Exception as e:
                done["search_err"] = str(e)

        th = threading.Thread(target=_search, daemon=True)
        th.start()
        time.sleep(0.05)  # small stagger so requests overlap

        t0 = time.monotonic()
        h = json.loads(urllib.request.urlopen(_REAL_URL + "/health", timeout=15).read())
        elapsed = time.monotonic() - t0
        th.join(timeout=20)

        self.assertNotIn("search_err", done, f"concurrent search failed: {done.get('search_err')}")
        self.assertIn("results", done.get("search", {}), "concurrent search returned no results key")
        self.assertEqual(h["status"], "ok")
        self.assertLess(elapsed, 5.0,
                        "/health blocked by concurrent /search (non-threaded server?)")

    def test_threaded_service_concurrent_searches(self):
        # Two concurrent searches against the REAL threaded service both succeed
        # (the handler is multi-threaded; one query doesn't serialize the other).
        _point_client(_REAL_URL, 15)
        results = {}

        def do(i, q):
            results[i] = kb_client.search(q)

        threads = [
            threading.Thread(target=do, args=(0, "where is my order")),
            threading.Thread(target=do, args=(1, "how do I return an item")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        self.assertTrue(results.get(0), "concurrent search 0 failed")
        self.assertTrue(results.get(1), "concurrent search 1 failed")


# --------------------------------------------------------------------------- #
# 8. DRAFT ENGINE unchanged — works with the service UP and DOWN.
# --------------------------------------------------------------------------- #
class TestDraftEngineContract(_EnvRestore):
    def test_draft_engine_service_up(self):
        _point_client(_REAL_URL, 15)
        d = draft_engine.generate_draft("Where is my order? Has it shipped yet?")
        self.assertFalse(d.kb_gap, "benign query should not be a KB gap (service up)")
        self.assertTrue(d.kb_sources, "draft should cite KB sources (service up)")
        self.assertTrue(d.should_post)
        # kb_chunks are dict views -> same list[KBChunk]-derived contract.
        self.assertIsInstance(d.kb_chunks, list)

    def test_draft_engine_service_down(self):
        # Port 1 (tcpmux) is reserved and never listening on localhost, giving
        # an immediate ConnectionRefused with no TOCTOU race. Same approach as
        # TestServiceDown.test_dead_port_falls_back_fast.
        _point_client("http://127.0.0.1:1", 3.0)

        d = draft_engine.generate_draft("Where is my order? Has it shipped yet?")
        # File fallback still grounds a real draft -> same contract, no crash.
        self.assertFalse(d.kb_gap, "benign query should not be a KB gap (file fallback)")
        self.assertTrue(d.kb_sources, "draft should cite KB sources via file fallback")
        self.assertTrue(d.should_post)


if __name__ == "__main__":
    import sys

    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\nKB_ISOLATION TEST OK")
        sys.exit(0)
    sys.exit(1)
