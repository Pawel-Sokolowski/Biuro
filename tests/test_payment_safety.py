"""Import walut, storna, daty księgowania oraz ograniczenia rozliczeń."""
from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
import uuid

from app.db.dao_conn import get_conn
from app.db.dao_invoices import create_or_update_invoice, mark_exported
from app.db.dao_payments import (
    PaymentError, _money, confirm_bank_payment, get_invoice_payment_summary,
    import_mt940_file, list_invoice_choices, list_unsettled_transfers, parse_ing_mt940, record_cash_payment,
    suggested_invoice_ids,
)
from tests.base import DbTestCase


def statement(number="1", account="PL44105000997603123456789123", currency="PLN", direction="C",
              value_date="260907", entry_date="0908", amount="123,00"):
    return (
        f":20:STATEMENT{number}\n:25:{account}\n:28C:{number}/1\n"
        f":60F:C260901{currency}0,00\n"
        f":61:{value_date}{entry_date}{direction}{amount}NTRFREF{number}\n:86:FV/001/09/2026\n"
        f":62F:C260908{currency}123,00\n"
    ).encode()


class Mt940SafetyTests(unittest.TestCase):
    def test_fingerprints_are_stable_for_separate_and_combined_statements(self):
        first = statement()
        second = statement("2", account="PL22105000997603123456789123", currency="EUR")
        separate = parse_ing_mt940(first) + parse_ing_mt940(second)
        combined = parse_ing_mt940(first + second)
        self.assertEqual([t["hash"] for t in separate], [t["hash"] for t in combined])
        self.assertEqual(combined[1]["currency"], "EUR")
        self.assertEqual(combined[0]["title"], "FV/001/09/2026")

    def test_booking_date_is_used_and_value_date_preserved_across_new_year(self):
        record = parse_ing_mt940(statement(value_date="261231", entry_date="0102"))[0]
        self.assertEqual(record["date"], "2027-01-02")
        self.assertEqual(record["value_date"], "2026-12-31")

    def test_unique_bank_reference_is_stable_across_overlapping_exports(self):
        original = statement().replace(b"NTRFREF1\n", b"NTRFREF1//BANK-UNIQUE\n")
        overlapping = original.replace(b":20:STATEMENT1", b":20:STATEMENT2").replace(b":28C:1/1", b":28C:2/1")
        overlapping = overlapping.replace(
            b":61:260907", b":61:2609050906C1,00NTRFOTHER//BANK-OTHER\n:86:Other\n:61:260907", 1
        )
        self.assertEqual(parse_ing_mt940(original)[0]["hash"], parse_ing_mt940(overlapping)[1]["hash"])

    def test_reversal_status_remains_distinguishable_from_incoming_payment(self):
        for direction, amount in (("C", "123.00"), ("D", "-123.00"), ("RC", "-123.00"), ("RD", "123.00")):
            with self.subTest(direction=direction):
                record = parse_ing_mt940(statement(direction=direction))[0]
                self.assertEqual(record["direction"], direction)
                self.assertEqual(record["amount"], Decimal(amount))

    def test_money_rejects_nonfinite_and_fractional_cents(self):
        for value in ("NaN", "sNaN", "Infinity", "-Infinity", "123.456"):
            with self.subTest(value=value), self.assertRaises(PaymentError):
                _money(value)
        with self.assertRaises(PaymentError):
            parse_ing_mt940(statement(amount="123,456"))

    def test_missing_currency_is_not_assumed_to_be_pln(self):
        with self.assertRaises(PaymentError):
            parse_ing_mt940(b":20:S\n:25:PL123\n:28C:1/1\n:61:260907C123,00NTRFREF\n:86:invoice")


class PaymentSafetyTests(DbTestCase):
    def invoice(self, issued=True):
        company = self.make_company()
        invoice_id = create_or_update_invoice(company, "2026-09", [{"nazwa": "Usługa", "ilosc": 1}], 100, 123)
        if issued:
            mark_exported([invoice_id], 1)
        return invoice_id

    def transfer(self, currency="PLN", direction="C"):
        conn = get_conn()
        try:
            row = conn.execute(
                "INSERT INTO bank_transakcje(hash,data_operacji,kwota,waluta,kierunek) "
                "VALUES (%s, '2026-09-08', 123.00, %s, %s) RETURNING id",
                (uuid.uuid4().hex, currency, direction),
            ).fetchone()
            conn.commit()
            return row[0]
        finally:
            conn.close()

    def test_draft_cannot_receive_bank_or_cash_payment(self):
        invoice = self.invoice(issued=False)
        transfer = self.transfer()
        with self.assertRaises(PaymentError):
            record_cash_payment(invoice, "2026-09-07", "1.00", 1)
        with self.assertRaises(PaymentError):
            confirm_bank_payment(transfer, invoice, "1.00", 1)

    def test_foreign_and_reversed_transfers_cannot_settle_pln_invoice(self):
        invoice = self.invoice()
        for currency, direction in (("EUR", "C"), ("PLN", "RD"), ("PLN", "RC"), ("PLN", None)):
            with self.subTest(currency=currency, direction=direction):
                transfer = self.transfer(currency, direction)
                with self.assertRaises(PaymentError):
                    confirm_bank_payment(transfer, invoice, "1.00", 1)
                self.assertNotIn(transfer, [row["id"] for row in list_unsettled_transfers(1)])
        self.assertEqual(get_invoice_payment_summary(invoice, 1)["paid"], Decimal("0.00"))

    def test_partial_and_final_cash_payments_have_exact_remaining_balance(self):
        invoice = self.invoice()
        record_cash_payment(invoice, "2026-09-07", "0.10", 1)
        record_cash_payment(invoice, "2026-09-08", "0.20", 1)
        summary = get_invoice_payment_summary(invoice, 1)
        self.assertEqual(summary["balance"], Decimal("122.70"))
        self.assertIsNone(summary["paid_on"])
        record_cash_payment(invoice, "2026-09-09", "122.70", 1)
        self.assertEqual(get_invoice_payment_summary(invoice, 1)["paid_on"], "2026-09-09")
        with self.assertRaises(PaymentError):
            record_cash_payment(invoice, "2026-09-09", "0.01", 1)

    def test_importing_combined_file_does_not_duplicate_separate_imports(self):
        first, second = statement(), statement("2")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "statement.sta"
            for content in (first, second):
                path.write_bytes(content)
                self.assertEqual(import_mt940_file(str(path), 1)["new"], 1)
            path.write_bytes(first + second)
            self.assertEqual(import_mt940_file(str(path), 1), {"all": 2, "new": 0, "duplicates": 2})

    def test_employee_without_finance_permission_cannot_add_payment(self):
        employee = self.make_employee()
        invoice = self.invoice()
        with self.assertRaises(PermissionError):
            record_cash_payment(invoice, "2026-09-07", "1.00", employee)

    def test_invoice_choice_balance_keeps_decimal_precision_for_suggestions(self):
        invoice = self.invoice()
        conn = get_conn()
        try:
            conn.execute("UPDATE faktury SET kwota_brutto=123.33, numer='FV/DECIMAL' WHERE id=%s", (invoice,))
            conn.commit()
        finally:
            conn.close()
        record_cash_payment(invoice, "2026-09-07", "100.11", 1)
        choice = next(row for row in list_invoice_choices(1) if row["id"] == invoice)
        self.assertEqual(choice["balance"], Decimal("23.22"))
        self.assertEqual(suggested_invoice_ids({"tytul": "FV/DECIMAL", "remaining": "23.22"}, [choice]), [invoice])
