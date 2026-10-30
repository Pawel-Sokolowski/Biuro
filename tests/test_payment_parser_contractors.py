"""ING: oddzielny tytuł, polskie kodowania i ostrożne filtrowanie nabywcy."""
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from app.db.dao_conn import get_conn
from app.db.dao_invoices import create_or_update_invoice, mark_exported
from app.db.dao_payments import (
    confirm_bank_payment, import_mt940_file, invoice_choices_for_transaction,
    list_unsettled_transfers, match_invoice_contractor, parse_ing_mt940,
)
from tests.base import DbTestCase

ACCOUNT = "44105000997603123456789123"


def ing_statement(purpose="FAKTURA VAT NR", name="JAN TESTOWY", marker="~"):
    text = (
        f":20:MT940\n:25:/PL{ACCOUNT}\n:28C:00225\n:60F:C261013PLN863,46\n"
        ":61:2610131013C1279,20S03497209074747\n:86:034\n"
        ":86:034~00XX20PRZELEW\n"
        f"~20{purpose}\n~21FV/0091/09/2026 - ZA~22FIRMA\n~23SP.J.~24\n~25\n"
        f"~29{ACCOUNT}~3010500099\n~317603123456789123~32{name}\n"
        f"~33UL. PRZYKŁADOWA 1~34034\n~38PL{ACCOUNT}~62ADRES CIĄG DALSZY\n~63\n"
        ":61:2610131013D10161,00S07664001699043\n:86:076\n"
    )
    return text.replace("~", marker)


def invoice(invoice_id, company_id, number, buyer, **kwargs):
    return dict(id=invoice_id, firma_id=company_id, numer=number, buyer=buyer,
                balance=Decimal("1279.20"), **kwargs)


class IngContractorParserTests(unittest.TestCase):
    def test_repeated_86_and_multiline_purpose_are_separate_from_sender_and_account(self):
        for marker in ("~", "?", ">"):
            with self.subTest(marker=marker):
                rows = parse_ing_mt940(ing_statement(marker=marker).encode("cp1250"))
                self.assertEqual(len(rows), 2)
                credit = rows[0]
                self.assertEqual(credit["date"], "2026-10-13")
                self.assertEqual(credit["amount"], Decimal("1279.20"))
                self.assertEqual(credit["currency"], "PLN")
                self.assertEqual(credit["title"], "FAKTURA VAT NR FV/0091/09/2026 - ZA FIRMA SP.J.")
                self.assertEqual(credit["contractor"], "JAN TESTOWY")
                self.assertEqual(credit["contractor_account"], ACCOUNT)
                self.assertEqual(credit["invoice_number"], "FV/0091/09/2026")
                self.assertNotIn(ACCOUNT, credit["title"])
                self.assertEqual(rows[1]["amount"], Decimal("-10161.00"))

    def test_polish_characters_have_explicit_overrides_for_ambiguous_single_byte_encodings(self):
        for encoding in ("utf-8", "cp1250", "iso-8859-2", "cp852"):
            with self.subTest(encoding=encoding):
                raw = ing_statement(purpose="Za Żółć Łęska Śląsk Źródło", name="Żółć Łęska Śląsk").encode(encoding)
                explicit = parse_ing_mt940(raw, encoding=encoding)[0]
                self.assertEqual(explicit["contractor"], "Żółć Łęska Śląsk")
                self.assertTrue(explicit["title"].startswith("Za Żółć Łęska "))
                if encoding in ("utf-8", "cp1250"):
                    self.assertEqual(parse_ing_mt940(raw)[0]["contractor"], "Żółć Łęska Śląsk")

    def test_correcting_decoding_does_not_change_transaction_identity(self):
        raw = ing_statement(purpose="Za Żółć Łęska Śląsk", name="Żółć Łęska Śląsk").encode("iso-8859-2")
        self.assertEqual(parse_ing_mt940(raw, "cp1250")[0]["hash"],
                         parse_ing_mt940(raw, "iso-8859-2")[0]["hash"])

    def test_full_account_38_has_priority_and_short_30_plus_31_is_not_used(self):
        text = ing_statement().replace(f"~29{ACCOUNT}", "~2911111111111111111111111111")
        self.assertEqual(parse_ing_mt940(text.encode("cp1250"))[0]["contractor_account"], ACCOUNT)
        text = text.replace(f"~38PL{ACCOUNT}", "~38")
        self.assertEqual(parse_ing_mt940(text.encode("cp1250"))[0]["contractor_account"], "11111111111111111111111111")
        text = text.replace("~2911111111111111111111111111", "~29")
        self.assertEqual(parse_ing_mt940(text.encode("cp1250"))[0]["contractor_account"], "")

    def test_structured_name_only_is_not_mistaken_for_payment_purpose(self):
        text = ing_statement().replace("~20FAKTURA VAT NR\n~21FV/0091/09/2026 - ZA~22FIRMA\n~23SP.J.~24\n~25\n", "")
        self.assertEqual(parse_ing_mt940(text.encode("cp1250"))[0]["title"], "")


class ContractorMatchingTests(unittest.TestCase):
    def test_exact_september_invoice_wins_over_different_person_payer_name(self):
        transaction = parse_ing_mt940(ing_statement().encode("cp1250"))[0]
        rows = [invoice(1, 10, "FV/0091/09/2026", "FIRMA SP.J."),
                invoice(2, 10, "FV/0100/10/2026", "FIRMA SP.J."),
                invoice(3, 20, "FV/0092/09/2026", "JAN TESTOWY")]
        match = match_invoice_contractor(transaction, rows)
        self.assertTrue(match["confident"])
        self.assertEqual(match["company_key"], ("firma", 10))
        self.assertEqual([row["id"] for row in invoice_choices_for_transaction(transaction, rows)], [1, 2])

    def test_duplicate_invoice_numbers_or_conflicting_explicit_nip_leave_manual_fallback(self):
        rows = [invoice(1, 10, "FV/001/09/2026", "ALFA", nip="1234567890"),
                invoice(2, 20, "FV/001/09/2026", "BETA", nip="9876543210")]
        transaction = {"tytul": "FV/001/09/2026"}
        self.assertFalse(match_invoice_contractor(transaction, rows)["confident"])
        self.assertEqual(invoice_choices_for_transaction(transaction, rows), rows)
        rows[1]["numer"] = "FV/002/09/2026"
        transaction["tytul"] = "FV/001/09/2026 NIP:9876543210"
        self.assertFalse(match_invoice_contractor(transaction, rows)["confident"])

    def test_batch_title_for_two_companies_does_not_choose_first(self):
        rows = [invoice(1, 10, "FV/001/09/2026", "ALFA"), invoice(2, 20, "FV/002/09/2026", "BETA")]
        self.assertFalse(match_invoice_contractor({"tytul": "FV/001/09/2026 oraz FV/002/09/2026"}, rows)["confident"])

    def test_account_and_exact_normalized_name_can_identify_contractor_without_invoice_number(self):
        rows = [invoice(1, 10, "FV/001/09/2026", "Żółć Sp. z o.o.", buyer_account="PL" + ACCOUNT),
                invoice(2, 20, "FV/002/09/2026", "Żółć Hurtownia")]
        self.assertEqual([r["id"] for r in invoice_choices_for_transaction({"kontrahent_rachunek": ACCOUNT}, rows)], [1])
        self.assertEqual([r["id"] for r in invoice_choices_for_transaction({"kontrahent": "Zolc spolka z ograniczona odpowiedzialnoscia"}, rows)], [1])
        self.assertFalse(match_invoice_contractor({"kontrahent": "Żółć"}, rows)["confident"])

    def test_person_name_matches_in_any_order_with_an_extra_middle_name(self):
        rows = [invoice(1, 10, "FV/001/09/2026", "Sosiw Krystian"),
                invoice(2, 20, "FV/002/09/2026", "Inny Krystian")]
        transaction = {"kontrahent": "KRYSTIAN RYSZARD SOSIW BART"}
        match = match_invoice_contractor(transaction, rows)
        self.assertTrue(match["confident"])
        self.assertEqual(match["resolved_contractor"], "Sosiw Krystian")
        self.assertEqual([row["id"] for row in invoice_choices_for_transaction(transaction, rows)], [1])

    def test_partial_person_name_must_still_identify_only_one_contractor(self):
        rows = [invoice(1, 10, "FV/001/09/2026", "Sosiw Krystian"),
                invoice(2, 20, "FV/002/09/2026", "Krystian Sosiw")]
        self.assertFalse(match_invoice_contractor(
            {"kontrahent": "KRYSTIAN RYSZARD SOSIW"}, rows,
        )["confident"])

    def test_manual_invoices_with_null_company_are_grouped_by_counterparty_id(self):
        rows = [invoice(1, None, "FV/001/09/2026", "ALFA", kontrahent_id=100),
                invoice(2, None, "FV/002/09/2026", "BETA", kontrahent_id=200),
                invoice(3, None, "FV/003/10/2026", "ALFA", kontrahent_id=100)]
        self.assertEqual([r["id"] for r in invoice_choices_for_transaction({"tytul": "FV/001/09/2026"}, rows)], [1, 3])


class ContractorImportTests(DbTestCase):
    def test_reimport_with_correct_encoding_refreshes_metadata_without_duplicate_financial_records(self):
        raw = ing_statement(purpose="Za Żółć Łęska Śląsk", name="Żółć Łęska Śląsk").encode("iso-8859-2")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ing.sta"
            path.write_bytes(raw)
            self.assertEqual(import_mt940_file(str(path), 1, "cp1250")["new"], 2)
            self.assertNotEqual(list_unsettled_transfers(1)[0]["kontrahent"], "Żółć Łęska Śląsk")
            report = import_mt940_file(str(path), 1, "iso-8859-2")
            self.assertEqual(report, {"all": 2, "new": 0, "duplicates": 2})
        entries = list_unsettled_transfers(1)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["kontrahent"], "Żółć Łęska Śląsk")
        self.assertEqual(entries[0]["kontrahent_rachunek"], ACCOUNT)
        self.assertTrue(entries[0]["tytul"].startswith("Za Żółć Łęska "))

    def test_correcting_encoding_does_not_change_description_of_an_allocated_transfer(self):
        raw = ing_statement(purpose="Za Żółć Śląsk", name="Żółć Śląsk").encode("iso-8859-2")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ing.sta"
            path.write_bytes(raw)
            import_mt940_file(str(path), 1, "cp1250")
            transfer = list_unsettled_transfers(1)[0]
            company = self.make_company("FIRMA TESTOWA")
            invoice_id = create_or_update_invoice(company, "2026-09", [{"nazwa": "Usługa", "ilosc": 1}], 100, 1279.2)
            mark_exported([invoice_id], 1)
            confirm_bank_payment(transfer["id"], invoice_id, "1.00", 1)
            self.assertEqual(import_mt940_file(str(path), 1, "iso-8859-2")["new"], 0)
        conn = get_conn()
        try:
            stored = conn.execute("SELECT kontrahent, kwota FROM bank_transakcje WHERE id=%s", (transfer["id"],)).fetchone()
            self.assertEqual(stored["kontrahent"], transfer["kontrahent"])
            self.assertEqual(stored["kwota"], Decimal("1279.20"))
        finally:
            conn.close()

