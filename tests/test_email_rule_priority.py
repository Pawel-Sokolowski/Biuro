import unittest

from app.email.email_rules import find_rule, rule_matches

SALDEO = {"nadawca_zawiera": "biuro@saldeo.pl", "target": "Saldeo"}
KLIENT_DO = {"do": "klient@firma.pl", "target": "Klient A"}
KLIENT_OD = {"nadawca_zawiera": "klient@firma.pl", "target": "Klient A"}


class RulePriorityTests(unittest.TestCase):
    def test_saldeo_mail_to_client_with_cc_goes_to_client_folder(self):
        header = {"sender": "biuro@saldeo.pl", "recipient": "klient@firma.pl, biuro@ksiegowosc.pl", "subject": "x"}
        for client_rule in (KLIENT_DO, KLIENT_OD):
            with self.subTest(rule=client_rule):
                self.assertEqual(find_rule([SALDEO, client_rule], header)["target"], "Klient A")

    def test_saldeo_mail_without_client_stays_in_saldeo(self):
        header = {"sender": "biuro@saldeo.pl", "recipient": "biuro@ksiegowosc.pl", "subject": "x"}
        self.assertEqual(find_rule([SALDEO, KLIENT_OD], header)["target"], "Saldeo")

    def test_outgoing_mail_to_client_matches_sender_rule(self):
        header = {"sender": "biuro@ksiegowosc.pl", "recipient": "klient@firma.pl", "subject": "x"}
        self.assertFalse(rule_matches(KLIENT_OD, header))
        self.assertEqual(find_rule([KLIENT_OD], header)["target"], "Klient A")

    def test_no_rule_returns_none(self):
        self.assertIsNone(find_rule([SALDEO], {"sender": "a@b.pl", "recipient": "c@d.pl", "subject": ""}))


if __name__ == "__main__":
    unittest.main()
