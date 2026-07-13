import sys
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


if __name__ == "__main__":
    unittest.main()
