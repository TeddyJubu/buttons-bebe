from __future__ import annotations

import ast
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parent


def mcp_tools(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
            ):
                names.add(node.name)
    return names


def load_function(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    node = next(
        item for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
    )
    namespace: dict = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


class ToolContractTests(unittest.TestCase):
    def test_redo_source_matches_installed_four_tool_contract(self) -> None:
        self.assertEqual(
            mcp_tools(TOOLS_DIR / "redo_mcp.py"),
            {"list_recent_returns", "get_returns_for_order", "get_return", "get_order"},
        )

    def test_redo_trim_preserves_current_refund_and_tracking_schema(self) -> None:
        trim = load_function(TOOLS_DIR / "redo_mcp.py", "_trim")
        fixture = {
            "id": "return-1",
            "status": "processing",
            "createdAt": "2026-07-13T00:00:00Z",
            "updatedAt": "2026-07-13T01:00:00Z",
            "completeWithNoAction": False,
            "order": {"id": "order-1", "name": "12345"},
            "compensationMethods": [{"type": "refund"}],
            "refunds": [{"amount": "10.00"}],
            "totals": {"refund": "10.00", "storeCredit": "0.00"},
            "shipments": [{"trackingNumber": "TRACK", "trackingUrl": "https://carrier.test"}],
            "exchange": {"itemCount": 0},
            "giftCards": [],
            "items": [{"id": "item-1", "status": "received"}],
            "source": {"emailAddress": "customer@example.test"},
        }
        result = trim(fixture)
        self.assertEqual(result["created_at"], fixture["createdAt"])
        self.assertEqual(result["updated_at"], fixture["updatedAt"])
        self.assertEqual(result["order_name"], "12345")
        self.assertEqual(result["compensation_methods"], fixture["compensationMethods"])
        self.assertEqual(result["refunds"], fixture["refunds"])
        self.assertEqual(result["totals"], fixture["totals"])
        self.assertEqual(result["shipments"], fixture["shipments"])
        self.assertEqual(result["exchange"], fixture["exchange"])
        self.assertNotIn("source", result)

    def test_gorgias_source_matches_installed_five_tool_contract(self) -> None:
        self.assertEqual(
            mcp_tools(TOOLS_DIR / "gorgias_mcp.py"),
            {
                "list_recent_tickets",
                "get_ticket",
                "get_ticket_messages",
                "get_customer",
                "search_customer",
            },
        )


if __name__ == "__main__":
    unittest.main()
