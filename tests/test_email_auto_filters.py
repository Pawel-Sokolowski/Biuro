"""Testy generowania filtrów z historii dwóch lat."""
import unittest
from unittest import mock

from app.email.email_auto_filters import (
    build_auto_filter_candidates, learn_filters_from_mail_history,
)
from app.email.email_rules import rule_matches
from tests.base import DbTestCase


def _folder(name, attributes=""):
    return {"server_name": name, "attributes": attributes, "delimiter": "."}


def _mail(folder, sender, recipient):
    return {"server_name": folder, "nadawca": sender, "odbiorca": recipient}


class AutoFilterCandidateTests(unittest.TestCase):
    def test_sender_recipient_and_internal_domains(self):
        folders = [_folder("INBOX"), _folder("INBOX.Sent", "\\Sent"),
                   _folder("INBOX.Klient A"), _folder("INBOX.Klient B")]
        rows = [
            _mail("INBOX.Klient A", "Jan <jan@klient.pl>", "Biuro <biuro@nasza.pl>"),
            _mail("INBOX.Klient A", "jan@klient.pl", "biuro@nasza.pl"),
            _mail("INBOX.Klient A", "biuro@nasza.pl", "Anna <anna@klient.pl>"),
            _mail("INBOX.Klient A", "biuro@nasza.pl", "anna@klient.pl"),
            _mail("INBOX.Klient A", "kontakt@nasza.pl", "biuro@nasza.pl"),
            _mail("INBOX.Klient A", "jan@wewnetrzna.pl", "biuro@nasza.pl"),
            _mail("INBOX.Klient A", "jan@gmail.com", "biuro@nasza.pl"),
            _mail("INBOX.Klient A", "jan@gmail.com", "biuro@nasza.pl"),
            _mail("INBOX.Sent", "biuro@nasza.pl", "mail@obcy.pl"),
            _mail("INBOX.Sent", "biuro@nasza.pl", "mail@obcy.pl"),
        ]
        candidates = build_auto_filter_candidates(
            rows, folders, ["biuro@nasza.pl"], "wewnetrzna.pl",
        )
        self.assertEqual({(x["address"], x["folder"]) for x in candidates}, {
            ("jan@klient.pl", "INBOX.Klient A"),
            ("anna@klient.pl", "INBOX.Klient A"),
            ("jan@gmail.com", "INBOX.Klient A"),
        })

    def test_conflicted_address_is_not_assigned_to_any_folder(self):
        folders = [_folder("INBOX.Klient A"), _folder("INBOX.Klient B")]
        rows = [
            _mail("INBOX.Klient A", "sporny@klient.pl", "biuro@nasza.pl"),
            _mail("INBOX.Klient A", "sporny@klient.pl", "biuro@nasza.pl"),
            _mail("INBOX.Klient B", "sporny@klient.pl", "biuro@nasza.pl"),
        ]
        self.assertEqual(
            build_auto_filter_candidates(rows, folders, ["biuro@nasza.pl"]), [],
        )

    def test_once_only_and_incoming_cc_recipients_are_ignored(self):
        folders = [_folder("INBOX.Klient")]
        rows = [
            _mail("INBOX.Klient", "raz@klient.pl", "inny@firma.pl, biuro@nasza.pl"),
            _mail("INBOX.Klient", "inny@firma.pl", "biuro@nasza.pl"),
        ]
        self.assertEqual(
            build_auto_filter_candidates(rows, folders, ["biuro@nasza.pl"]), [],
        )

    def test_public_mail_provider_not_excluded_by_office_gmail(self):
        folders = [_folder("INBOX.Klient")]
        rows = [
            _mail("INBOX.Klient", "klient@gmail.com", "biuro@gmail.com"),
            _mail("INBOX.Klient", "klient@gmail.com", "biuro@gmail.com"),
        ]
        self.assertEqual(
            build_auto_filter_candidates(rows, folders, ["biuro@gmail.com"]),
            [{"address": "klient@gmail.com", "folder": "INBOX.Klient"}],
        )

    def test_exact_address_match_prevents_similar_names(self):
        rule = {"od": "jan@klient.pl", "do": ""}
        self.assertTrue(rule_matches(
            rule, {"sender": "Jan <JAN@klient.pl>", "recipient": "biuro@nasza.pl"},
        ))
        self.assertFalse(rule_matches(
            rule, {"sender": "niejan@klient.pl", "recipient": "biuro@nasza.pl"},
        ))
        to_rule = {"do": "jan@klient.pl"}
        self.assertTrue(rule_matches(
            to_rule, {"recipient": "A <anna@firma.pl>, Jan <JAN@klient.pl>"},
        ))
        self.assertFalse(rule_matches(to_rule, {"recipient": "niejan@klient.pl"}))


class AutoFilterDbTests(DbTestCase):
    def test_creates_two_rules_only_once_and_excludes_internal(self):
        from app.db.dao_conn import get_conn
        from app.email.email_storage import save_folder, get_folder_id, save_message_header

        conn = get_conn()
        try:
            account = conn.execute(
                "INSERT INTO konta_email (email) VALUES ('biuro@nasza.pl') RETURNING id"
            ).fetchone()[0]
            conn.commit()
        finally:
            conn.close()
        folder = "INBOX.Klient A"
        save_folder(account, "INBOX", "Odebrane", ".", "")
        save_folder(account, folder, "Klient A", ".", "")
        fid = get_folder_id(account, folder)
        for uid, sender in (
            ("1", "Jan <jan@klient.pl>"),
            ("2", "jan@klient.pl"),
            ("3", "sekretariat@nasza.pl"),
        ):
            save_message_header(
                account, fid, uid, f"<{uid}@klient.pl>", sender,
                "biuro@nasza.pl", "Temat", "2999-01-01 10:00",
                False, False,
            )

        with mock.patch("app.db.dao_app_settings.get_setting", return_value=""):
            stats = learn_filters_from_mail_history(account)
            again = learn_filters_from_mail_history(account)
        self.assertEqual(stats["addresses"], 1)
        self.assertEqual(stats["added"], 2)
        self.assertEqual(again["added"], 0)

        conn = get_conn()
        try:
            rules = conn.execute(
                'SELECT od, "do", folder_docelowy FROM reguly_filtrow WHERE konto_id = %s',
                (account,),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rules), 2)
        self.assertEqual({r["folder_docelowy"] for r in rules}, {folder})
        self.assertEqual({r["od"] for r in rules if r["od"]}, {"jan@klient.pl"})
        self.assertEqual({r["do"] for r in rules if r["do"]}, {"jan@klient.pl"})


if __name__ == "__main__":
    unittest.main()
