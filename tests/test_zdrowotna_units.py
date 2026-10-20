from app.db import dao_companies as dc
from app.db.dao_conn import get_conn
from app.invoices.faktury_logic import FakturyLogic
from tests.base import DbTestCase


class ZdrowotnaUnitsTests(DbTestCase):
    def _company(self, name):
        conn = get_conn()
        fid = conn.execute(
            "INSERT INTO firmy (nazwa, glowny_cennik, zdrowotna) VALUES (%s, 'KPIR_T_5_260', 60) RETURNING id",
            (name,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO cenniki (klucz, typ, cena_netto_ksiegowosc, cena_brutto_ksiegowosc, "
            "cena_za_dokument_ponad_limit, zdrowotna, netto, brutto) "
            "VALUES ('KPIR_T_5_260', 'KPIR', 260, 319.8, 5, 60, 320, 393.6) ON CONFLICT DO NOTHING"
        )
        conn.commit()
        conn.close()
        return fid

    def _add_partners(self, fid, n):
        conn = get_conn()
        for i in range(n):
            conn.execute("INSERT INTO udzialowcy (firma_id, imie_pierwsze, nazwisko) VALUES (%s, 'A', %s)", (fid, f"N{i}"))
        conn.commit()
        conn.close()

    def test_default_invoicing_and_units(self):
        solo = self._company("Solo")
        civil = self._company("Cywilna")
        self._add_partners(civil, 2)
        conn = get_conn()
        self.assertEqual(conn.execute("SELECT fakturowac FROM firmy WHERE id=%s", (solo,)).fetchone()[0], 1)
        conn.close()
        self.assertEqual(dc.count_zdrowotna_units(solo), 1)
        self.assertEqual(dc.count_zdrowotna_units(civil), 2)
        pos = FakturyLogic().generate_invoice_positions(civil, "2026-10")
        zdr = [p for p in pos if p["nazwa"] == "Zdrowotna"][0]
        self.assertEqual((zdr["ilosc"], zdr["netto"]), (2, 120))

