from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import live_api


class MessageDisplayTests(unittest.TestCase):
    def test_soft_wrapped_incoming_message_reads_as_one_sentence(self):
        raw = ("Hi, I realized that I don't need the item from  Order 12345  . Is\n"
               "there a way to drop it off locally? I'm visiting tomorrow and it's\n"
               "easier than mailing the parcel back.")
        result = live_api.message({
            'id': 1, 'preferred_content': raw,
            'preferred_content_field': 'stripped_text',
            'sender': {'name': 'Example Customer'},
        })
        self.assertEqual(
            result['body'],
            "Hi, I realized that I don't need the item from Order 12345. Is "
            "there a way to drop it off locally? I'm visiting tomorrow and it's "
            "easier than mailing the parcel back.",
        )

    def test_greeting_paragraphs_and_lists_keep_their_breaks(self):
        raw = "Hi team,\nPlease review this request.\n\n- Keep this item\n- Return that one"
        self.assertEqual(
            live_api.display_content({
                'preferred_content': raw,
                'preferred_content_field': 'stripped_text',
            }),
            raw,
        )

    def test_html_content_is_left_for_the_browser_parser(self):
        raw = "<div>Hello<br>World</div>"
        self.assertEqual(
            live_api.display_content({
                'preferred_content': raw,
                'preferred_content_field': 'stripped_html',
            }),
            raw,
        )


if __name__ == '__main__':
    unittest.main()
