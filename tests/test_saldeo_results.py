import unittest
from email.message import EmailMessage

from app.utils.saldeo_results import is_quarterly, parse_saldeo_settlement
from app.utils.saldeo_scanner import scan_saldeo_messages
from tests.base import DbTestCase

HTML = """
<html><body><p>Nazwa firmy</p>
<h3>Rozliczenie za wrzesień 2026</h3>
<table>
<tr><th>Składnik rozliczenia</th><th>Kwota</th><th>Kwota narastająco</th></tr>
<tr><td>przychód z działalności</td><td>48&nbsp;430,00 PLN</td><td>295 981,90 PLN</td></tr>
<tr><td>koszty działalności</td><td>28 085,16 PLN</td><td>370 532,74 PLN</td></tr>
<tr><td>dochód / strata z działalności</td><td>20 344,84 PLN</td><td>-74 550,84 PLN</td></tr>
<tr><td>różnica podatku VAT</td><td>0,00 PLN (do zwrotu)<br>4 450,00 PLN (do przeniesienia)</td><td></td></tr>
</table>
<p>Kwoty podatków do zapłaty</p>
<table>
<tr><th>Podatek</th><th>Kwota</th><th>Okres deklaracji</th><th>Termin</th><th>Opis</th></tr>
<tr><td>PIT5</td><td>1 234,50 PLN</td><td>Wrzesień 2026</td><td>20-10-2026</td><td></td></tr>
<tr><td>VAT7</td><td>0,00 PLN</td><td>III kwartał 2026</td><td>20-10-2026</td><td></td></tr>
</table>
<p>Mikrorachunek podatkowy ... PL08 1010 0071 2222 8521 2244 5600</p>
</body></html>
"""


def _msg(html: str):
    m = EmailMessage()
    m["From"] = "Saldeo <no-reply@saldeo.com>"
    m.set_content("tekst")
    m.add_alternative(html, subtype="html")
    return m


class ParserTests(unittest.TestCase):
    def test_parse(self):
        from app.utils.saldeo_results import html_to_text
        d = parse_saldeo_settlement(html_to_text(HTML))
        self.assertEqual(d["nazwa"], "Nazwa firmy")
        self.assertEqual(d["miesiac"], "2026-09")
        self.assertEqual((d["przychod"], d["koszty"], d["dochod"]), (48430.0, 28085.16, 20344.84))
        self.assertEqual((d["pit_kwota"], d["vat_kwota"], d["cit_kwota"]), (1234.5, 0.0, None))
        self.assertEqual(d["vat_okres"], "III kwartał 2026")
        self.assertTrue(is_quarterly(d["vat_okres"]))
        self.assertFalse(is_quarterly(d["pit_okres"]))
        self.assertEqual(d["pit_termin"], "20-10-2026")
        self.assertIn("4 450,00 PLN (do przeniesienia)", d["vat_opis"])

    def test_not_settlement(self):
        self.assertIsNone(parse_saldeo_settlement("Dzień dobry, faktura w załączniku"))


class ScanTests(DbTestCase):
    def test_scan(self):
        from app.db.dao_conn import get_conn
        firma = self.make_company("Nazwa firmy")
        conn = get_conn()
        try:
            acc = conn.execute("INSERT INTO konta_email (email) VALUES ('a@b.pl') RETURNING id").fetchone()["id"]
            fol = conn.execute(
                "INSERT INTO foldery_email (konto_id, server_name) VALUES (%s, 'INBOX') RETURNING id", (acc,)
            ).fetchone()["id"]
            conn.execute(
                "INSERT INTO wiadomosci_email (konto_id, folder_id, uid, nadawca) VALUES (%s, %s, '1', 'Saldeo <biuro@saldeo.pl>')",
                (acc, fol),
            )
            conn.commit()
        finally:
            conn.close()
        res = scan_saldeo_messages(acc, fetch=lambda *a: _msg(HTML))
        self.assertEqual(res["saved"], 1)
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM rozliczenia_saldeo WHERE firma_id = %s", (firma,)).fetchone()
            self.assertEqual(row["miesiac"], "2026-09")
            self.assertEqual(row["przychod"], 48430.0)
        finally:
            conn.close()
        self.assertEqual(scan_saldeo_messages(acc, fetch=lambda *a: None)["saved"], 0)

if __name__ == "__main__":
    unittest.main()
