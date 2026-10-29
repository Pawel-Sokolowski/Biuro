"""Nieprzeczytane w ukrytych folderach nie zwiększają czerwonego badge'a."""
import unittest

from app.email.email_storage import filter_visible_unread_counts


class HiddenUnreadTests(unittest.TestCase):
    def test_excludes_hidden_and_entire_subtree(self):
        folders = [
            {"id": 1, "server_name": "INBOX", "delimiter": "."},
            {"id": 2, "server_name": "INBOX.Ukryty", "delimiter": "."},
            {"id": 3, "server_name": "INBOX.Ukryty.Podfolder", "delimiter": "."},
            {"id": 4, "server_name": "INBOX.Widoczny", "delimiter": "."},
            {"id": 5, "server_name": "INBOX.Ukryty2", "delimiter": "."},
        ]
        counts = {1: 5, 2: 7, 3: 11, 4: 2, 5: 1}
        visible = filter_visible_unread_counts(folders, counts, {"INBOX.Ukryty"})
        self.assertEqual(visible, {1: 5, 4: 2, 5: 1})
        self.assertEqual(sum(visible.values()), 8)

    def test_slash_separator_and_missing_folder_ids(self):
        folders = [
            {"id": 10, "server_name": "INBOX", "delimiter": "/"},
            {"id": 11, "server_name": "INBOX/2 Archiwum", "delimiter": "/"},
            {"id": 12, "server_name": "INBOX/2 Archiwum/Firma", "delimiter": "/"},
            {"id": 13, "server_name": "INBOX/3 Firmy", "delimiter": "/"},
        ]
        self.assertEqual(
            filter_visible_unread_counts(folders, {10: 1, 11: 3, 12: 8, 13: 6, 99: 5},
                                         {"INBOX/2 Archiwum"}),
            {10: 1, 13: 6},
        )

    def test_no_hidden_folders_retains_all_known_unread(self):
        folders = [{"id": 1, "server_name": "INBOX", "delimiter": "."}]
        self.assertEqual(filter_visible_unread_counts(folders, {1: 7, 999: 20}), {1: 7})


if __name__ == "__main__":
    unittest.main()
