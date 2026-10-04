"""Offline contract tests for the QA KB proxy and production MCP response."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import qa_mcp_server
from qa_safety import filter_search_outcome, scenario_fixture

REPO = Path(__file__).resolve().parent.parent
KB_SCRIPTS = REPO / "kb" / "scripts"
POLICY_FILE = "policies/shipping.md"
PRIVATE_MARKERS = ("PRIVATE-CUSTOMER-991", "PRIVATE-OWNER-NOTICE-992")


def search_outcome(*, status="degraded", notice_state="unavailable", index_state="healthy", results=None):
    return {
        "status": status,
        "notice_board": {
            "state": notice_state,
            "active_count": None if notice_state == "unavailable" else 0,
            "codes": ["notice_read_failed"] if notice_state == "unavailable" else [],
            "operator_action": "Inspect the Notice Board. Active owner overrides may still exist."
            if notice_state == "unavailable" else "",
        },
        "index": {
            "state": index_state,
            "codes": ["index_open_failed"] if index_state == "unavailable" else [],
        },
        "results": results or [],
    }


def mcp_value(server_result):
    if not isinstance(server_result, tuple) or len(server_result) != 2:
        raise AssertionError("Expected the JSON-mode MCP content and structured result tuple")
    structured = server_result[1]
    if not isinstance(structured, dict):
        raise AssertionError("Expected structured MCP tool output")
    if set(structured) == {"result"}:
        return structured["result"]
    if set(structured) == {"status", "notice_board", "index", "results"}:
        return structured
    raise AssertionError("Expected a SearchOutcome-shaped MCP tool result")


class StubSession:
    def __init__(self, result):
        self.result = result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        return None

    async def call_tool(self, name, arguments):
        if name != "search_kb" or arguments != {"query": "shipping help", "k": 25}:
            raise AssertionError("QA proxy requested an unexpected KB tool call")
        return self.result


class SearchOutcomeContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fixture = self.root / "fixture.json"
        self.fixture.write_text(json.dumps(scenario_fixture(
            {"id": "QA-CONTRACT", "subject": "Shipping", "message": "When will it ship?", "email": "qa@example.com"}, 1
        )))
        self.allowlist = self.root / "allowlist.json"
        self.allowlist.write_text(json.dumps([POLICY_FILE]))
        self.audit = self.root / "audit.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def call_fixture_proxy(self):
        server = qa_mcp_server.create_server("buttonsbebe_kb", 28771, self.fixture,
                                              self.audit, self.allowlist, "fixture")
        return mcp_value(asyncio.run(server.call_tool(
            "search_kb", {"query": "shipping help", "k": 5}
        )))

    def call_real_proxy(self, outcome):
        server = qa_mcp_server.create_server("buttonsbebe_kb", 28772, self.fixture,
                                              self.audit, self.allowlist, "policies-only")
        result = SimpleNamespace(isError=False, structuredContent=outcome, content=[])
        requested = []

        @asynccontextmanager
        async def no_network(url, **kwargs):
            requested.append(url)
            yield object(), object(), None

        with patch.object(qa_mcp_server, "streamablehttp_client", no_network), \
             patch.object(qa_mcp_server, "ClientSession", return_value=StubSession(result)):
            safe = mcp_value(asyncio.run(server.call_tool(
                "search_kb", {"query": "shipping help", "k": 5}
            )))
        self.assertEqual(requested, ["http://127.0.0.1:8077/mcp"])
        return safe

    def test_fixture_returns_the_production_top_level_contract(self):
        safe = self.call_fixture_proxy()
        self.assertEqual(set(safe), {"status", "notice_board", "index", "results"})
        self.assertEqual(safe["status"], "healthy")
        self.assertEqual(safe["notice_board"], {
            "state": "healthy", "active_count": 0, "codes": [], "operator_action": ""
        })
        self.assertEqual(safe["index"], {"state": "healthy", "codes": []})
        self.assertEqual(len(safe["results"]), 1)
        self.assertTrue(safe["results"][0]["qa_fixture"])
        self.assertEqual(safe["results"][0]["file"], "policies/qa-fixture.md")

    def test_real_response_preserves_health_and_filters_only_result_passages(self):
        outcome = search_outcome(results=[
            {"file": POLICY_FILE, "category": "policies", "status": "confirmed",
             "title": "Shipping", "heading": "Timing",
             "text": "Contact qa.person@example.com at +1 (555) 123-4567.",
             "sensitive": True, "source": "policy", "tags": "shipping", "score": 0.9},
            {"file": "tickets/private.md", "category": "tickets", "status": "confirmed",
             "text": PRIVATE_MARKERS[0]},
            {"file": "notices/notices.json", "category": "notices", "status": "confirmed",
             "text": PRIVATE_MARKERS[1]},
            {"file": POLICY_FILE, "category": "policies", "status": "draft",
             "text": "Unconfirmed policy text"},
        ])
        safe = self.call_real_proxy(outcome)
        self.assertEqual(set(safe), {"status", "notice_board", "index", "results"})
        self.assertEqual(safe["status"], "degraded")
        self.assertEqual(safe["notice_board"], outcome["notice_board"])
        self.assertEqual(safe["index"], outcome["index"])
        self.assertEqual(len(safe["results"]), 1)
        row = safe["results"][0]
        self.assertEqual(set(row), {"file", "category", "status", "sensitive", "title", "heading", "text"})
        self.assertEqual(row["file"], POLICY_FILE)
        self.assertTrue(row["sensitive"])
        serialized = json.dumps(safe)
        for marker in (*PRIVATE_MARKERS, "qa.person@example.com", "123-4567", "source", "tags"):
            self.assertNotIn(marker, serialized)
        self.assertIn("[email removed]", serialized)
        self.assertIn("[phone or identifier removed]", serialized)

    def test_health_contract_accepts_healthy_degraded_and_unavailable_states(self):
        cases = (
            search_outcome(status="healthy", notice_state="healthy", index_state="healthy"),
            search_outcome(status="degraded", notice_state="unavailable", index_state="healthy"),
            search_outcome(status="unavailable", notice_state="unavailable", index_state="unavailable"),
        )
        for outcome in cases:
            with self.subTest(status=outcome["status"]):
                safe, filtered = filter_search_outcome(outcome, {POLICY_FILE})
                self.assertEqual(set(safe), {"status", "notice_board", "index", "results"})
                self.assertEqual(safe["status"], outcome["status"])
                self.assertEqual(safe["notice_board"], outcome["notice_board"])
                self.assertEqual(safe["index"], outcome["index"])
                self.assertEqual(filtered, 0)

    def test_invalid_health_diagnostics_and_status_mismatches_fail_closed(self):
        malformed = search_outcome()
        malformed["notice_board"]["operator_action"] = "Private customer note: " + PRIVATE_MARKERS[0]
        with self.assertRaises(ValueError):
            filter_search_outcome(malformed, {POLICY_FILE})
        malformed = search_outcome()
        malformed["status"] = "healthy"
        with self.assertRaises(ValueError):
            filter_search_outcome(malformed, {POLICY_FILE})

    def test_contradictory_source_health_fails_closed(self):
        cases = []
        for source in ('notice_board', 'index'):
            malformed = search_outcome(status='healthy', notice_state='healthy')
            malformed[source]['codes'] = ['notice_read_failed' if source == 'notice_board' else 'index_open_failed']
            cases.append(malformed)
            malformed = search_outcome(status='unavailable', index_state='unavailable')
            malformed[source]['codes'] = []
            cases.append(malformed)
        malformed = search_outcome(status='degraded', notice_state='degraded')
        malformed['notice_board']['codes'] = ['notice_read_failed']
        cases.append(malformed)
        for malformed in cases:
            with self.subTest(outcome=malformed), self.assertRaises(ValueError):
                filter_search_outcome(malformed, {POLICY_FILE})

    def test_notice_warning_must_match_availability(self):
        cases = [search_outcome(), search_outcome(status='healthy', notice_state='healthy')]
        cases[0]['notice_board']['operator_action'] = ''
        cases[1]['notice_board']['operator_action'] = 'Inspect the Notice Board. Active owner overrides may still exist.'
        for malformed in cases:
            with self.subTest(outcome=malformed), self.assertRaises(ValueError):
                filter_search_outcome(malformed, {POLICY_FILE})

    def test_degraded_index_with_failure_diagnostic_remains_valid(self):
        outcome = search_outcome(status='degraded', notice_state='healthy', index_state='degraded')
        outcome['index']['codes'] = ['vector_lookup_failed']
        safe, filtered = filter_search_outcome(outcome, {POLICY_FILE})
        self.assertEqual(safe, outcome)
        self.assertEqual(filtered, 0)

    @unittest.skipUnless(importlib.util.find_spec("lancedb"), "prepared KB runtime is required for the production MCP contract")
    def test_actual_production_mcp_shape_is_accepted_by_qa_proxy(self):
        production_outcome = search_outcome(results=[
            {"file": POLICY_FILE, "category": "policies", "status": "confirmed",
             "title": "Shipping", "heading": "Timing", "text": "Shipping takes one to two days.",
             "sensitive": False, "source": "policy", "tags": "shipping", "score": 0.9},
            {"file": "tickets/private.md", "category": "tickets", "status": "confirmed",
             "text": PRIVATE_MARKERS[0]},
        ])
        with patch.object(sys, "path", [str(KB_SCRIPTS), *sys.path]):
            import kb_mcp_server
        with patch.object(kb_mcp_server, "search", return_value=production_outcome):
            production_result = asyncio.run(kb_mcp_server.mcp.call_tool(
                "search_kb", {"query": "shipping help", "k": 25}
            ))
        self.assertIsInstance(production_result, tuple)
        production_structured = production_result[1]
        self.assertEqual(set(production_structured), {"status", "notice_board", "index", "results"})

        safe = self.call_real_proxy(production_structured)
        self.assertEqual(safe["status"], production_outcome["status"])
        self.assertEqual(safe["notice_board"], production_outcome["notice_board"])
        self.assertEqual(safe["index"], production_outcome["index"])
        self.assertEqual([row["file"] for row in safe["results"]], [POLICY_FILE])
        self.assertNotIn(PRIVATE_MARKERS[0], json.dumps(safe))


if __name__ == "__main__":
    unittest.main()
