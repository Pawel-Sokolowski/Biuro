import unittest
from email.message import EmailMessage

from app.email.email_helpers import (
    extract_html, extract_text, get_attachments, normalize_folder_name, parse_message, safe_header,
)


def build_message() -> bytes:
    msg = EmailMessage()
    msg["From"] = "Jan <jan@example.com>"
    msg["To"] = "biuro@example.com"
    msg["Subject"] = "Zażółć gęślą jaźń"
    msg.set_content("Treść tekstowa")
    msg.add_alternative("<p>Treść <b>HTML</b></p>", subtype="html")
    msg.add_attachment(b"abc", maintype="application", subtype="pdf", filename="faktura.pdf")
    return bytes(msg)


class EmailHelperTests(unittest.TestCase):
    def test_parse_extract_body_and_attachments(self):
        msg = parse_message(build_message())
        self.assertIn("Treść tekstowa", extract_text(msg))
        self.assertIn("<b>HTML</b>", extract_html(msg))
        attachments = get_attachments(msg)
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["filename"], "faktura.pdf")

    def test_encoded_subject_is_decoded(self):
        raw = "=?utf-8?b?WmHFvMOzxYLEhyBnxJnFm2zEhSBqYcW6xYQ=?="
        self.assertEqual(safe_header(raw), "Zażółć gęślą jaźń")

    def test_none_header_is_empty(self):
        self.assertEqual(safe_header(None), "")

    def test_known_folder_names_are_normalized(self):
        self.assertEqual(normalize_folder_name("INBOX"), normalize_folder_name("INBOX"))
        self.assertTrue(normalize_folder_name("INBOX"))


if __name__ == "__main__":
    unittest.main()
