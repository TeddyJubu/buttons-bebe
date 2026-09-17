from __future__ import annotations

import ast
import os
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from helpdesk.client import assert_query_only
from helpdesk.dispatch import WRITE_TOOLS, invoke
from helpdesk.errors import HelpdeskError

FORBIDDEN_SNIPPETS = (
    "shpat_",
    "shpss_",
    "gaia",
    "ask gaia",
    "#7c3aed",
    "gorgias purple",
    "gorgias_mcp",
    "fake_gorgias_mcp",
)


class SafetyTests(unittest.TestCase):
    def test_tree_has_no_tokens_pii_or_gaia(self) -> None:
        blobs: list[str] = []
        for path in (ROOT / "helpdesk").rglob("*.py"):
            blobs.append(path.read_text(encoding="utf-8").lower())
        readme = ROOT / "README.md"
        if readme.is_file():
            blobs.append(readme.read_text(encoding="utf-8").lower())
        text = "\n".join(blobs)
        for snippet in FORBIDDEN_SNIPPETS:
            self.assertNotIn(snippet, text, snippet)

    def test_does_not_import_gorgias_wrappers(self) -> None:
        for path in (ROOT / "helpdesk").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn("gorgias", alias.name.lower())
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn("gorgias", node.module.lower())
                    # Aliases too: `from x import gorgias_thing as y` would
                    # smuggle a wrapper past the module-name check (report 12,
                    # action 6). The bridge package's own dormant adapters are
                    # the sanctioned exceptions; anything else named gorgias*
                    # is a wrapper import.
                    for alias in node.names:
                        if node.module == "bridge":
                            self.assertIn(alias.name, {"config", "router", "email_out", "gorgias_api"})
                        else:
                            self.assertNotIn("gorgias", alias.name.lower())

    def test_mutations_refused_even_if_flag_on(self) -> None:
        previous = os.environ.get("SHOPIFY_MUTATIONS_ENABLED")
        os.environ["SHOPIFY_MUTATIONS_ENABLED"] = "1"
        try:
            with self.assertRaises(HelpdeskError):
                assert_query_only("mutation Refund { refundCreate { refund { id } } }")
            payload = invoke("helpdesk.refund", {"orderId": "gid://shopify/Order/1"})
            self.assertEqual(payload["error"], "forbidden")
            for tool in ("helpdesk.send", "helpdesk.cancel"):
                blocked = invoke(tool, {})
                self.assertEqual(blocked["error"], "forbidden")
            self.assertEqual(WRITE_TOOLS, frozenset({"helpdesk.send", "helpdesk.refund", "helpdesk.cancel"}))
            gate = invoke("helpdesk.write_gate_status", {})
            self.assertTrue(gate["ok"])
            self.assertIn("refund", gate["refused"])
            self.assertIn("cancel", gate["refused"])
            self.assertFalse(gate["mutationsEnabled"])
        finally:
            if previous is None:
                os.environ.pop("SHOPIFY_MUTATIONS_ENABLED", None)
            else:
                os.environ["SHOPIFY_MUTATIONS_ENABLED"] = previous

    def test_bare_ticket_number_rejected_on_rail(self) -> None:
        payload = invoke(
            "helpdesk.get_order",
            {"shop": "demo-helpdesk.example", "orderId": "1001"},
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "bad_request")


if __name__ == "__main__":
    unittest.main()
