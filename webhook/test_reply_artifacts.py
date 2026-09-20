"""Issue #43: quoted history and signature garbage must not reach the bubble.

`message_text()` falls back to raw `body_text`/`body_html`/`text` whenever
Gorgias does not render `stripped_text`, and those raw bodies carry the
mobile signature and the whole quoted previous message — glued into one
run-on blob when the sender's client sent no line breaks. The stripper runs
at ingest: the verbatim original stays in `webhook_events.raw_payload`, the
processor and the inbox read clean text.
"""
import unittest

from bb_webhook.message_content import message_text


class StripReplyArtifactTests(unittest.TestCase):
    def test_the_clients_own_example_is_cleaned(self):
        blob = ("Kin kin brown wool skirt i made mistake i need a 12 please edit order"
                "Sent from my Galaxy -------- Original message --------"
                "From: Buttons Bebe <customerservice@buttonsbebe.com> Date: 9/11/26 4:10 PM (GMT-05:00) "
                "To: e***a@gmail.com Subject: Order 10322954 confirmed")
        self.assertEqual(
            message_text({'body_text': blob}),
            "Kin kin brown wool skirt i made mistake i need a 12 please edit order")

    def test_each_marker_family_is_stripped(self):
        own = "My parcel arrived damaged and I would like a refund."
        cases = [
            own + "\n\nSent from my iPhone",
            own + "\n\n-------- Original message --------\nFrom: Buttons Bebe <hello@bb.com>\nSubject: Re: your order",
            own + "\n\n----- Original Message -----\nFrom: Buttons Bebe <hello@bb.com>",
            own + "\n\n-------- Forwarded message --------\n> earlier text\n\nsome rant",
            own + "\n\nOn Mon, Jul 20, 2026 at 9:14 AM Buttons Bebe Support <hello@bb.com> wrote:\n> we are on it",
            own + "\n> quoted line one\n> quoted line two",
        ]
        for body in cases:
            with self.subTest(body=body[:40]):
                self.assertEqual(message_text({'body_text': body}), own)

    def test_glued_separator_with_no_space_is_still_a_boundary(self):
        # cubic: a client can glue "---- Original message ----" directly to
        # the last word with no space at all.
        blob = ("Please edit order-------- Original message --------"
                "From: Buttons Bebe <customerservice@buttonsbebe.com> Subject: Order 1001")
        self.assertEqual(message_text({'body_text': blob}), "Please edit order")

    def test_legit_header_lines_are_not_quote_headers(self):
        # cubic: a customer's own From:/To: lines must survive.
        body = "Here is the answer.\nFrom: my account page\nTo: whoever needs it"
        self.assertEqual(message_text({'body_text': body}), body)

    def test_customer_prose_quoting_a_date_bearing_line_survives(self):
        # cubic: "On July 8 my wife wrote: call me back" is the customer
        # talking about a message, not a mail client quoting one.
        body = "Need help.\nOn July 8 my wife wrote: call me back please"
        self.assertEqual(message_text({'body_text': body}), body)
        body2 = "Need help.\nOn 7/20 my friend wrote: call me back please"
        self.assertEqual(message_text({'body_text': body2}), body2)

    def test_signature_only_body_returns_empty_not_the_signature(self):
        # cubic: an all-signature (or signature+quote) body must not fall
        # back to restoring the artifacts.
        self.assertEqual(message_text({'body_text': 'Sent from my Galaxy'}), '')
        self.assertEqual(message_text({'body_text': 'Sent from my Galaxy\n> quoted line'}), '')

    def test_prose_containing_sent_from_my_is_not_a_signature(self):
        # cubic: "It was sent from my store" is prose, not a footer.
        body = "It was sent from my store in Tel Aviv"
        self.assertEqual(message_text({'body_text': body}), body)

    def test_begin_forwarded_message_is_a_quote_marker(self):
        body = "Need help.\nBegin forwarded message:\n> old text"
        self.assertEqual(message_text({'body_text': body}), "Need help.")

    def test_html_blockquote_is_quote_content_even_without_headers(self):
        html = ('<p>Please help me with a new size.</p>'
                '<blockquote>Thanks for reaching out, we are on it</blockquote>')
        self.assertEqual(message_text({'body_html': html}), "Please help me with a new size.")

    def test_the_bare_text_fallback_key_strips_the_same_artifacts(self):
        # cubic: the legacy `text` key is a branch too.
        self.assertEqual(message_text({'text': 'Need a new size.\nSent from my iPhone'}), 'Need a new size.')

    def test_no_marker_leaves_the_text_alone(self):
        own = "On Friday I ordered a gift set. Your colleague wrote: we will chase it. This is ridiculous!!!"
        self.assertEqual(message_text({'body_text': own}), own)
        self.assertEqual(message_text({'body_text': "Thanks,\nJane"}), "Thanks,\nJane")

    def test_mid_sentence_wrote_is_not_a_quote_header(self):
        body = "Your colleague wrote: we will chase it. Still waiting!"
        self.assertEqual(message_text({'body_text': body}), body)

    def test_bottom_posted_prose_under_a_quote_survives(self):
        body = "> we are looking into it\n\nWHERE IS IT I HAVE HAD ENOUGH"
        self.assertEqual(
            message_text({'body_text': body}),
            "WHERE IS IT I HAVE HAD ENOUGH")

    def test_top_posted_separator_keeps_fresh_prose_below(self):
        # cubic: a client can top-post the quote, fresh words below — the
        # separator itself must not nuke them when it appears on top.
        body = "-------- Forwarded message --------\n> earlier text\n\nRANT HERE"
        self.assertEqual(message_text({'body_text': body}), "RANT HERE")

    def test_html_text_node_keeps_its_own_line_breaks(self):
        # cubic: a literal newline inside a non-quote text node is the
        # customer's own line structure — it must not glue words together.
        self.assertEqual(message_text({'body_html': '<p>Line one\nLine two</p>'}),
                         'Line one\nLine two')

    def test_dropped_quote_leaves_one_blank_line_not_a_gap(self):
        # cubic: quote lines are dropped whole, but their paragraph
        # boundaries must not widen into a run of blank paragraphs —
        # in HTML blockquotes and plain text alike.
        html = '<p>Own words.</p><blockquote><p>q one</p><p>q two</p></blockquote><p>More own.</p>'
        self.assertEqual(message_text({'body_html': html}), 'Own words.\n\nMore own.')
        self.assertEqual(message_text({'body_text': 'own\n\n> quoted\n\nmore'}),
                         'own\n\nmore')

    def test_an_all_quoted_message_falls_back_to_the_whole_text(self):
        body = "> I want a refund, my order arrived damaged"
        self.assertEqual(message_text({'body_text': body}), body)

    def test_html_path_strips_the_same_artifacts(self):
        html = ("<p>Please change it to a 12.</p><p>Sent from my Galaxy</p>"
                "<div>-------- Original message --------</div>"
                "<div>From: Buttons Bebe &lt;hello@bb.com&gt;</div>")
        self.assertEqual(
            message_text({'body_html': html}),
            "Please change it to a 12.")

    def test_gorgias_stripped_text_gets_the_signature_pass_too(self):
        # The issue names this case: even Gorgias's stripped text can keep
        # the mobile signature footer.
        self.assertEqual(
            message_text({'stripped_text': "New question\nSent from my Galaxy", 'body_text': 'whatever'}),
            "New question")

    def test_attachments_and_sign_offs_survive(self):
        body = "Please see the photo.\n\nThanks,\nJane"
        self.assertEqual(message_text({'body_text': body}), body)
