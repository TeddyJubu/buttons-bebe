import sys
import re
import unittest
from pathlib import Path

KB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KB_DIR / "scripts"))

from sensitivity import (  # noqa: E402
    is_sensitive_metadata,
    is_sensitive_tags,
    normalize_tags,
)


def read_tags(relative_path):
    for line in (KB_DIR / relative_path).read_text(encoding="utf-8").splitlines():
        if line.startswith("tags: [") and line.endswith("]"):
            return [tag.strip() for tag in line[7:-1].split(",") if tag.strip()]
    raise AssertionError(f"No inline tags found in {relative_path}")


def searchable_chunks(markdown: str):
    """Mirror the indexer's level-two-heading chunk boundary without dependencies."""
    heading = None
    lines = []
    started = False
    for line in markdown.splitlines():
        if re.match(r"^##\s", line):
            if started and any(part.strip() for part in lines):
                yield heading or "", "\n".join(lines).strip()
            heading = line.lstrip("#").strip()
            lines = []
            started = True
        elif started:
            lines.append(line)
    if started and any(part.strip() for part in lines):
        yield heading or "", "\n".join(lines).strip()


class SensitivityTaxonomyTests(unittest.TestCase):
    def test_normalizes_common_tag_variants(self):
        self.assertEqual(
            normalize_tags([" Wrong_Item ", "ANGRY CUSTOMER", "refund"]),
            {"wrong-item", "angry-customer", "refund"},
        )
        self.assertEqual(
            normalize_tags("damaged, missing_item"),
            {"damaged", "missing-item"},
        )
        self.assertEqual(normalize_tags(123), {"123"})

    def test_documented_safety_topics_are_sensitive(self):
        topics = {
            "refund",
            "refund-window",
            "escalate",
            "chargeback",
            "dispute",
            "damaged",
            "defect",
            "wrong-item",
            "missing-item",
            "missing-accessory",
            "angry-customer",
            "upset-customer",
            "manager-request",
            "privacy",
            "cancel",
            "cancellation",
            "cancellations",
            "address-change",
            "final-sale",
            "final-sale-exception",
            "lost-package",
            "stolen-package",
        }
        for topic in topics:
            with self.subTest(topic=topic):
                self.assertTrue(is_sensitive_tags([topic]))

    def test_explicit_sensitive_metadata_cannot_be_bypassed_by_benign_tags(self):
        self.assertTrue(
            is_sensitive_metadata({"tags": ["shipping"], "sensitive": True})
        )
        self.assertTrue(
            is_sensitive_metadata({"tags": ["shipping"], "sensitive": "yes"})
        )
        self.assertTrue(
            is_sensitive_metadata({"tags": ["refund"], "sensitive": False})
        )

    def test_malformed_safety_metadata_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "tags metadata"):
            is_sensitive_metadata({"tags": {"refund": False}})
        with self.assertRaisesRegex(ValueError, "only strings"):
            is_sensitive_metadata({"tags": [{"refund": False}]})
        with self.assertRaisesRegex(ValueError, "sensitive metadata"):
            is_sensitive_metadata({"tags": ["shipping"], "sensitive": "maybe"})

    def test_benign_topics_stay_non_sensitive(self):
        self.assertFalse(is_sensitive_tags(["shipping", "tracking", "sizing", "fabric"]))

    def test_real_high_risk_content_is_flagged(self):
        files = [
            "intents/intent-10-zip-code-address-correction.md",
            "intents/intent-09-measurements.md",
            "intents/intent-15-wrong-item-received.md",
            "intents/intent-16-damaged-item-received.md",
            "intents/intent-12-final-sale-exchange-exception.md",
            "policies/lost-or-stolen-package.md",
            "policies/warranty-and-defects.md",
            "policies/return-and-exchange-policy.md",
            "policies/return-windows-and-refund-tiers.md",
            "policies/refunds-and-disputes.md",
            "policies/restocking-fees.md",
            "policies/escalation-and-edge-cases.md",
        ]
        for relative_path in files:
            with self.subTest(path=relative_path):
                self.assertTrue(is_sensitive_tags(read_tags(relative_path)))

    def test_real_benign_content_is_not_flagged(self):
        self.assertFalse(is_sensitive_tags(read_tags("policies/shipping-policy.md")))
        self.assertFalse(
            is_sensitive_tags(read_tags("policies/sensitive-draft-policy.md"))
        )

    def test_operational_language_is_guarded_inside_each_searchable_chunk(self):
        """Never rely on another chunk to communicate the read-only boundary."""
        operational = re.compile(
            r"\b(?:notify|contact|email)\s+(?:the\s+)?(?:warehouse|brand|vendor)\b"
            r"|\b(?:ship|send)\s+(?:the\s+)?(?:correct|replacement)\s+item\b"
            r"|\b(?:issue|process|send|provide)\s+(?:a\s+)?(?:refund|store credit|"
            r"prepaid (?:return )?label|return label)\b"
            r"|\b(?:i|we)(?:'ve| have)?\s+(?:switched|changed|updated|corrected|"
            r"cancelled|canceled|refunded|issued|shipped|sent|contacted|provided)\b"
            r"|\bhere(?:'s| is)\s+(?:a\s+)?(?:prepaid\s+)?return label\b",
            re.IGNORECASE,
        )
        same_chunk_guard = re.compile(
            r"\b(?:AI|agent) must not\b|\b(?:human|staff handoff|authorized staff)\b"
            r"|\b(?:do not|never|avoid) (?:claim|promise|issue|process|present)\b"
            r"|\bonly (?:after|when)\b.{0,160}\b(?:confirm|verif|complet)",
            re.IGNORECASE | re.DOTALL,
        )
        failures = []
        for folder in ("intents", "faq", "policies", "tickets"):
            for path in sorted((KB_DIR / folder).rglob("*.md")):
                if path.name.lower() == "readme.md":
                    continue
                for heading, chunk in searchable_chunks(path.read_text(encoding="utf-8")):
                    if operational.search(chunk) and not same_chunk_guard.search(chunk):
                        failures.append(f"{path.relative_to(KB_DIR)}::{heading}")
        self.assertEqual(failures, [], "unguarded operational KB chunks")


if __name__ == "__main__":
    unittest.main()
