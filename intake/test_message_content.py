"""Canonical stripped_text wrapping and saved-contract reads."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intake.message_content import intake_from_message, normalize_message


def _line(length):
    unit = "please confirm this order address today"
    text = (unit * 6)[:length]
    if text.endswith(" "):
        text = text[:-1] + "x"
    return text


class MessageContentTests(unittest.TestCase):
    def test_stripped_text_joins_only_a_60_to_90_soft_wrap(self):
        for length in (60, 75, 90):
            source = _line(length) + "\nout today"
            joined = _line(length) + " out today"
            result = normalize_message({"stripped_text": source})
            self.assertEqual(result["display_text"], joined)
            self.assertEqual(result["current_text"], joined)
            self.assertEqual(result["original_content"], source)
        for length in (59, 91):
            source = _line(length) + "\nout today"
            result = normalize_message({"stripped_text": source})
            self.assertEqual(result["display_text"], source)
            self.assertEqual(result["original_content"], source)
        self.assertEqual(
            normalize_message({"stripped_text": _line(70) + "\r\nout today"})["display_text"],
            _line(70) + " out today",
        )
        self.assertEqual(
            normalize_message({"stripped_text": _line(70) + "\nOut today"})["display_text"],
            _line(70) + "\nOut today",
        )
        stopped = _line(69) + "."
        self.assertEqual(
            normalize_message({"stripped_text": stopped + "\nout today"})["display_text"],
            stopped + "\nout today",
        )

    def test_stripped_text_keeps_lists_paragraphs_and_urls(self):
        samples = (
            _line(70) + "\n- next item",
            _line(70) + "\n* next item",
            _line(70) + "\n1. next item",
            _line(70) + "\n\nnext paragraph",
            _line(70) + "\n indented line",
            ("a" * 52) + "https://example.com/a" + "\nout today",
            _line(70) + "\nhttps://example.com/track",
            _line(70) + "\nwww.example.com/track",
        )
        for source in samples:
            self.assertGreaterEqual(len(source.split("\n")[0].rstrip()), 60)
            self.assertLessEqual(len(source.split("\n")[0].rstrip()), 90)
            result = normalize_message({"stripped_text": source})
            self.assertEqual(result["display_text"], source, source)
            self.assertEqual(result["original_content"], source)

    def test_space_collapse_is_stripped_text_only(self):
        raw = "Hello  ,  world"
        self.assertEqual(normalize_message({"stripped_text": raw})["display_text"], "Hello, world")
        self.assertEqual(normalize_message({"stripped_text": raw})["original_content"], raw)
        self.assertEqual(normalize_message({"body_text": raw})["display_text"], raw)
        wrapped = _line(70) + "\nout today"
        self.assertEqual(normalize_message({"body_text": wrapped})["display_text"], wrapped)
        self.assertEqual(normalize_message({"body_html": "<p>" + wrapped + "</p>"})["display_text"], wrapped)

    def test_saved_contract_is_kept_and_preferred_is_not_a_body(self):
        message = {
            "preferred_content": "NOT A RAW BODY",
            "preferred_content_field": "body_text",
            "stripped_text": "current line",
            "display_text": "saved history",
            "current_text": "saved current",
            "display_source": "body_text",
            "current_source": "stripped_text",
            "original_content": "saved raw",
            "original_field": "body_text",
            "history_available": True,
            "source_truncated": False,
            "cleanup_version": "intake-1",
        }
        saved = intake_from_message(message)
        self.assertEqual(saved["display_text"], "saved history")
        self.assertEqual(saved["current_text"], "saved current")
        self.assertEqual(saved["original_content"], "saved raw")
        self.assertTrue(saved["history_available"])
        self.assertEqual(message["preferred_content"], "NOT A RAW BODY")
        for broken in (
            {**message, "cleanup_version": "stale"},
            {**message, "history_available": 1},
            {**message, "display_text": None},
        ):
            read = intake_from_message(broken)
            self.assertEqual(read["current_text"], "current line")
            self.assertEqual(read["display_text"], "current line")
            self.assertFalse(read["history_available"])
            self.assertNotIn("NOT A RAW BODY", read["display_text"])
            self.assertNotIn("NOT A RAW BODY", read["original_content"])


if __name__ == "__main__":
    unittest.main()
