import json
from concurrent.futures import ThreadPoolExecutor

from app.db.dao_conn import get_conn
from app.db.dao_invoices import (
    add_invoice_item_for_service, delete_draft_invoice, get_invoice_by_id,
    get_invoice_positions, mark_exported, update_invoice_draft, validate_invoice_extra_services_ready,
)
from app.db.dao_services import add_company_extra_service
from app.db.dao_tasks import save_task
from app.invoices.faktury_logic import FakturyLogic
from tests.base import DbTestCase


class SupplementaryInvoiceTests(DbTestCase):
    MONTH = "2026-09"

    def setUp(self):
        super().setUp()
        self.company = self.make_company("Klient", fakturowac=1, stala_kwota_ksiegowosc=100)
        with get_conn() as conn:
            self.service = conn.execute(
                "INSERT INTO uslugi_dodatkowe (nazwa, cena_netto, stawka_vat, kategoria) "
                "VALUES ('Dodatkowa czynność', 60, '23', 'dodatkowe') RETURNING id"
            ).fetchone()[0]
        self.logic = FakturyLogic()

    def add_service(self, quantity=1, task=None):
        return add_company_extra_service(self.company, self.MONTH, self.service, quantity, 60, True,
                                         zadanie_id=task, actor_id=1)

    def generate(self):
        return self.logic.generate_and_save_invoice(self.company, self.MONTH)

    def issue_base(self, with_extra=False):
        if with_extra:
            self.add_service()
        invoice = self.generate()["id"]
        mark_exported([invoice], 1)
        return invoice

    def invoices(self):
        with get_conn() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM faktury WHERE firma_id = %s ORDER BY id", (self.company,)
            ).fetchall()]

    def test_normal_month_generation_adds_only_new_extras_after_issued_base_with_extras(self):
        base = self.issue_base(with_extra=True)
        before = get_invoice_by_id(base)
        later = self.add_service(quantity=2)
        result = self.logic.generate_and_save_invoices_for_month(self.MONTH)
        self.assertFalse(result["errors"])
        self.assertEqual(result["created"], 1)
        invoices = self.invoices()
        self.assertEqual(len(invoices), 2)
        supplement = invoices[1]
        self.assertEqual((supplement["dodatkowa"], supplement["reczna"], supplement["rodzaj"]), (1, 1, "faktura"))
        positions = json.loads(supplement["pozycje_json"])
        self.assertEqual([p["extra_service_row_id"] for p in positions], [later])
        self.assertEqual(supplement["kwota_netto"], 120)
        self.assertEqual(get_invoice_by_id(base), before)

    def test_repeated_generation_reuses_draft_and_preserves_editor_changes(self):
        self.issue_base()
        self.add_service()
        first = self.generate()
        positions = get_invoice_positions(first["id"])
        positions[0].update(nazwa="Opis uzgodniony z klientem", cena_netto=75)
        update_invoice_draft(first["id"], positions, numer="DOD/09/2026")
        second = self.generate()
        self.assertEqual(second["id"], first["id"])
        self.assertFalse(second["created"])
        self.assertEqual(get_invoice_positions(first["id"])[0]["nazwa"], "Opis uzgodniony z klientem")
        self.assertEqual(get_invoice_by_id(first["id"])["kwota_netto"], 75)
        self.add_service(quantity=2)
        third = self.generate()
        self.assertEqual(third["id"], first["id"])
        self.assertEqual(get_invoice_by_id(first["id"])["kwota_netto"], 195)
        self.assertEqual(len(self.invoices()), 2)

    def test_concurrent_generators_reserve_activity_once_and_reuse_same_draft(self):
        self.issue_base()
        service = self.add_service()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.generate(), range(2)))
        self.assertEqual(results[0]["id"], results[1]["id"])
        self.assertEqual(sum(bool(result["created"]) for result in results), 1)
        self.assertEqual([p["extra_service_row_id"] for p in get_invoice_positions(results[0]["id"])], [service])
        self.assertEqual(len(self.invoices()), 2)

    def test_later_completed_activity_gets_next_supplement_after_previous_is_issued(self):
        base = self.issue_base(with_extra=True)
        self.add_service()
        first = self.generate()["id"]
        mark_exported([first], 1)
        first_before = get_invoice_by_id(first)
        later = self.add_service(quantity=3)
        result = self.logic.generate_and_save_invoices_for_month(self.MONTH)
        self.assertFalse(result["errors"])
        self.assertEqual(result["created"], 1)
        second = self.invoices()[-1]["id"]
        self.assertNotIn(second, (base, first))
        self.assertEqual([p["extra_service_row_id"] for p in get_invoice_positions(second)], [later])
        self.assertEqual(get_invoice_by_id(first), first_before)
        mark_exported([second], 1)
        self.assertTrue(self.generate()["skipped"])
        self.assertEqual(len(self.invoices()), 3)

    def test_pending_and_cancelled_tasks_are_not_billed_but_completed_one_is(self):
        task = save_task("Do wykonania", firma_id=self.company, actor_id=1)
        pending = self.add_service(task=task)
        base = self.issue_base()
        self.assertFalse(any(p.get("extra_service_row_id") == pending for p in get_invoice_positions(base)))
        self.assertTrue(self.generate()["skipped"])
        save_task("Do wykonania", status="Zakończone", firma_id=self.company, task_id=task, actor_id=1)
        supplement = self.generate()["id"]
        self.assertEqual([p["extra_service_row_id"] for p in get_invoice_positions(supplement)], [pending])
        save_task("Do wykonania", status="W toku", firma_id=self.company, task_id=task, actor_id=1)
        with self.assertRaisesRegex(ValueError, "zakończ"):
            validate_invoice_extra_services_ready([supplement])
        with self.assertRaisesRegex(ValueError, "zakończ"):
            mark_exported([supplement], 1)
        save_task("Do wykonania", status="Zakończone", firma_id=self.company, task_id=task, actor_id=1)
        validate_invoice_extra_services_ready([supplement])
        self.assertFalse(get_invoice_by_id(supplement)["eksportowana"])

    def test_historic_issued_json_references_are_reserved_before_new_generation(self):
        base = self.issue_base(with_extra=True)
        with get_conn() as conn:
            conn.execute("DELETE FROM faktury_uslugi_dodatkowe")
        later = self.add_service()
        supplement = self.generate()["id"]
        self.assertEqual([p["extra_service_row_id"] for p in get_invoice_positions(supplement)], [later])
        with get_conn() as conn:
            invoice_ids = [row[0] for row in conn.execute(
                "SELECT invoice_id FROM faktury_uslugi_dodatkowe ORDER BY extra_service_row_id"
            ).fetchall()]
        self.assertEqual(invoice_ids, [base, supplement])

    def test_legacy_service_without_row_reference_is_not_billed_twice(self):
        base = self.issue_base(with_extra=True)
        with get_conn() as conn:
            positions = get_invoice_positions(base)
            for position in positions:
                position.pop("extra_service_row_id", None)
            conn.execute("UPDATE faktury SET pozycje_json = %s WHERE id = %s", (json.dumps(positions), base))
            conn.execute("DELETE FROM faktury_uslugi_dodatkowe")
        later = self.add_service()
        supplement = self.generate()["id"]
        self.assertEqual([p["extra_service_row_id"] for p in get_invoice_positions(supplement)], [later])

    def test_deleting_supplement_draft_releases_activities_for_regeneration(self):
        self.issue_base()
        service = self.add_service()
        first = self.generate()["id"]
        delete_draft_invoice(first)
        second = self.generate()["id"]
        self.assertNotEqual(first, second)
        self.assertEqual([p["extra_service_row_id"] for p in get_invoice_positions(second)], [service])

    def test_empty_repeat_and_annual_prepaid_company_do_not_create_empty_invoices(self):
        with get_conn() as conn:
            conn.execute("UPDATE firmy SET fakturowac = 0 WHERE id = %s", (self.company,))
        self.add_service()
        base = self.generate()["id"]
        mark_exported([base], 1)
        self.assertTrue(self.generate()["skipped"])
        self.assertEqual(len(self.invoices()), 1)
        later = self.add_service()
        result = self.logic.generate_and_save_invoices_for_month(self.MONTH)
        self.assertFalse(result["errors"])
        self.assertEqual(result["created"], 1)
        self.assertEqual([p["extra_service_row_id"] for p in get_invoice_positions(self.invoices()[1]["id"])], [later])

    def test_timer_activity_linked_to_pending_task_is_retained_without_draft(self):
        task = save_task("Praca ze stoperem", firma_id=self.company, actor_id=1)
        self.assertIsNone(add_invoice_item_for_service(
            self.company, self.service, 0.5, self.MONTH, zadanie_id=task,
        ))
        self.assertEqual(self.invoices(), [])
        with get_conn() as conn:
            row = conn.execute("SELECT ilosc, zadanie_id FROM uslugi_dodatkowe_firma").fetchone()
        self.assertEqual((row["ilosc"], row["zadanie_id"]), (0.5, task))
