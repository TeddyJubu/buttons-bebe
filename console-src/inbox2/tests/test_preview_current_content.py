"""get_ticket list previews must use the selected message's current text."""
from contextlib import closing
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.gorgias_content import curate_message
import test_detail_preview_freshness as detail_setup


T6, T7 = detail_setup.T6, detail_setup.T7
OLD = curate_message({
    "id": 1, "created_datetime": T6, "channel": "email", "public": True,
    "body_text": "OLDER CUSTOMER REQUEST",
})
SIGNATURE = curate_message({
    "id": 2, "created_datetime": T7, "channel": "email", "public": True,
    "body_html": "<blockquote>HISTORICAL REQUEST FOR REFUND</blockquote>"
                 "<p>Sent from my iPhone</p>",
})
CURRENT_REPLY = "Please change the delivery date to Friday."
BOTTOM_POSTED = curate_message({
    "id": 2, "created_datetime": T7, "channel": "email", "public": True,
    "body_html": "<blockquote>HISTORICAL REQUEST FOR REFUND</blockquote>"
                 "<p>" + CURRENT_REPLY + "</p>",
})


class PreviewCurrentContent(unittest.TestCase):
    def setUp(self):
        self.fixture = detail_setup.DetailPreviewFreshness(
            "test_fresh_head_is_not_stale_and_is_cached"
        )
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def seed_prior_preview(self):
        prior = detail_setup.api.summary(detail_setup.raw(T7, "LIST EXCERPT"))
        prior.update(
            snippet=OLD["display_text"], previewMessageId="1",
            previewProvenance={
                "source": "message", "truncated": False, "cleanupVersion": "intake-1",
            },
        )
        with closing(detail_setup.api.database()) as db, db:
            detail_setup.api.cache_summary(db, prior, "g1", preserve_preview=False)

    def assertPreview(self, out, *, message_id, snippet, truncated, cleanup="intake-1"):
        self.assertEqual(out["snippet"], snippet)
        self.assertEqual(out["previewMessageId"], message_id)
        self.assertEqual(out["previewProvenance"], {
            "source": "message", "truncated": truncated, "cleanupVersion": cleanup,
        })
        saved, _ = self.fixture.stored()
        self.assertEqual(saved["snippet"], snippet)
        self.assertEqual(saved["previewMessageId"], message_id)
        self.assertEqual(saved["previewProvenance"], out["previewProvenance"])

    def test_signature_only_newest_public_message_uses_empty_current_text(self):
        self.seed_prior_preview()
        self.assertEqual(SIGNATURE["current_text"], "")
        self.assertNotEqual(SIGNATURE["display_text"], "")
        out = self.fixture.detail(
            detail_setup.raw(T7, "LIST EXCERPT"), [OLD, SIGNATURE]
        )
        self.assertPreview(
            out, message_id="2", snippet="", truncated=False,
        )
        self.fixture.sync(detail_setup.raw(T7, "REFRESHED LIST EXCERPT"))
        saved, _ = self.fixture.stored()
        self.assertEqual((saved["snippet"], saved["previewMessageId"]), ("", "2"))
        self.assertEqual(saved["previewProvenance"], out["previewProvenance"])

    def test_bottom_posted_newest_reply_uses_current_text_not_display_text(self):
        self.assertEqual(BOTTOM_POSTED["current_text"], CURRENT_REPLY)
        self.assertNotEqual(BOTTOM_POSTED["display_text"], CURRENT_REPLY)
        out = self.fixture.detail(
            detail_setup.raw(T7, "LIST EXCERPT"), [OLD, BOTTOM_POSTED]
        )
        self.assertPreview(
            out, message_id="2", snippet=CURRENT_REPLY, truncated=False,
        )

    def test_missing_current_text_uses_legacy_body(self):
        legacy = {
            "id": 2, "created_datetime": T7, "channel": "email", "public": True,
            "display_text": "LEGACY DISPLAY FALLBACK",
        }
        self.assertNotIn("current_text", legacy)
        out = self.fixture.detail(detail_setup.raw(T7, "LIST EXCERPT"), [OLD, legacy])
        self.assertPreview(
            out, message_id="2", snippet="LEGACY DISPLAY FALLBACK",
            truncated=False, cleanup=None,
        )

    def test_newest_explicitly_blank_message_clears_old_preview(self):
        self.seed_prior_preview()
        blank = {
            "id": 2, "created_datetime": T7, "channel": "email", "public": True,
            "display_text": "", "current_text": "", "cleanup_version": "intake-1",
        }
        out = self.fixture.detail(detail_setup.raw(T7, "LIST EXCERPT"), [OLD, blank])
        self.assertPreview(out, message_id="2", snippet="", truncated=False)

    def test_current_text_truncation_and_provenance_follow_same_message(self):
        current = "CURRENT REPLY " + ("x" * 330)
        newest = curate_message({
            "id": 2, "created_datetime": T7, "channel": "email", "public": True,
            "body_html": "<blockquote>OLD " + ("q" * 350) + "</blockquote><p>"
                         + current + "</p>",
        })
        self.assertGreater(len(newest["current_text"]), 300)
        self.assertNotEqual(newest["display_text"][:300], newest["current_text"][:300])
        out = self.fixture.detail(detail_setup.raw(T7, "LIST EXCERPT"), [OLD, newest])
        self.assertPreview(
            out, message_id="2", snippet=newest["current_text"][:300], truncated=True,
        )


if __name__ == "__main__":
    unittest.main()
