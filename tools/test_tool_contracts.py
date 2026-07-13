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


class ToolContractTests(unittest.TestCase):
    def test_redo_source_matches_installed_four_tool_contract(self) -> None:
        self.assertEqual(
            mcp_tools(TOOLS_DIR / "redo_mcp.py"),
            {"list_recent_returns", "get_returns_for_order", "get_return", "get_order"},
        )

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
