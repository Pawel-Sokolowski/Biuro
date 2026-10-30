"""Czynności dodatkowe dziedziczą wykonanie i uprawnienia z zadań."""
import psycopg

from app.db.dao_conn import get_conn
from app.db.dao_invoices import create_or_update_invoice, mark_exported
from app.db.dao_services import (
    add_company_extra_service, create_extra_activity, create_task_for_extra_activity,
    link_extra_activity_task, list_extra_activities,
)
from app.db.dao_tasks import delete_task, delete_tasks, list_tasks, save_task, set_tasks_status
from tests.base import DbTestCase


class ExtraActivityTaskTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.company = self.make_company("Czynności")
        conn = get_conn()
        try:
            self.service = conn.execute(
                "INSERT INTO uslugi_dodatkowe(nazwa,cena_netto,stawka_vat) "
                "VALUES ('Dodatkowe zgłoszenie', 50, '23') RETURNING id"
            ).fetchone()[0]
            conn.commit()
        finally:
            conn.close()

    def activity(self, **kwargs):
        return create_extra_activity(
            self.company, "2026-09", self.service, 1, 50, True,
            actor_id=kwargs.pop("actor_id", 1), task_title=kwargs.pop("task_title", "Zgłoszenie"), **kwargs,
        )

    def row(self, service_id, viewer_id=1):
        return next(row for row in list_extra_activities(viewer_id) if row["id"] == service_id)

    def issue(self, service_id, legacy=False):
        positions = [{"nazwa": "Zgłoszenie", "ilosc": 1, "netto": 50, "vat": 23, "brutto": 61.5,
                      "cena_netto": 50, "usluga_id": self.service, "extra_service_row_id": service_id}]
        if legacy:
            positions[0].pop("extra_service_row_id")
        invoice = create_or_update_invoice(self.company, "2026-09", positions, 50, 61.5)
        mark_exported([invoice], 1)
        return invoice

    def test_pending_done_cancelled_are_always_derived_from_task_status(self):
        activity = self.activity()
        row = self.row(activity)
        self.assertEqual(row["state"], "pending")
        self.assertEqual(row["task_title"], "Zgłoszenie")
        self.assertEqual(row["fakturowac"], 1)
        task = row["zadanie_id"]
        set_tasks_status([task], "W toku", actor_id=1)
        self.assertEqual(self.row(activity)["state"], "pending")
        set_tasks_status([task], "Zakończone", actor_id=1)
        self.assertEqual(self.row(activity)["state"], "done")
        set_tasks_status([task], "Anulowane", actor_id=1)
        self.assertEqual(self.row(activity)["state"], "cancelled")
        self.assertEqual(self.row(activity)["fakturowac"], 1)

    def test_historical_unlinked_entries_remain_distinct_and_can_get_new_task(self):
        activity = add_company_extra_service(self.company, "2026-09", self.service, 1, 50, True)
        self.assertEqual(self.row(activity)["state"], "unlinked")
        task = create_task_for_extra_activity(activity, 1, pracownik_id=1)
        self.assertEqual(self.row(activity)["zadanie_id"], task)
        self.assertEqual(self.row(activity)["state"], "pending")
        with self.assertRaises(ValueError):
            create_task_for_extra_activity(activity, 1)

    def test_atomic_create_with_invalid_service_does_not_leave_orphan_task(self):
        before = len(list_tasks(viewer_id=1))
        with self.assertRaises(psycopg.IntegrityError):
            create_extra_activity(self.company, "2026-09", -1, 1, 50, True,
                                  actor_id=1, task_title="Nie może pozostać")
        self.assertEqual(len(list_tasks(viewer_id=1)), before)

    def test_task_of_another_company_cannot_be_linked(self):
        other = self.make_company("Inna firma")
        task = save_task("Obca firma", firma_id=other, actor_id=1)
        activity = add_company_extra_service(self.company, "2026-09", self.service, 1, 50, True)
        with self.assertRaises(ValueError):
            link_extra_activity_task(activity, task, 1)
        self.assertIsNone(self.row(activity)["zadanie_id"])

    def test_visibility_and_linking_respect_private_task_permissions(self):
        owner = self.make_employee()
        other = self.make_employee(email="other@example.com")
        activity = self.activity(actor_id=owner, task_employee_id=owner)
        self.assertEqual(self.row(activity, owner)["state"], "pending")
        self.assertNotIn(activity, [row["id"] for row in list_extra_activities(other)])
        legacy = add_company_extra_service(self.company, "2026-09", self.service, 1, 50, True)
        with self.assertRaises(PermissionError):
            link_extra_activity_task(legacy, self.row(activity)["zadanie_id"], other)

    def test_user_cannot_create_activity_assigned_to_another_employee(self):
        employee = self.make_employee()
        other = self.make_employee(email="other@example.com")
        with self.assertRaises(PermissionError):
            self.activity(actor_id=employee, task_employee_id=other)
        self.assertEqual(list_extra_activities(employee), [])

    def test_linked_task_cannot_change_company_or_be_deleted(self):
        activity = self.activity()
        task = self.row(activity)["zadanie_id"]
        with self.assertRaises(ValueError):
            save_task("Zmieniona firma", firma_id=self.make_company("Druga"), task_id=task, actor_id=1)
        with self.assertRaises(ValueError):
            delete_task(task, actor_id=1)
        self.assertEqual(delete_tasks([task], actor_id=1), 0)
        self.assertEqual(self.row(activity)["state"], "pending")

    def test_billed_completed_task_cannot_be_reopened_via_single_or_bulk_edit(self):
        activity = self.activity(task_status="Zakończone")
        task = self.row(activity)["zadanie_id"]
        self.issue(activity)
        with self.assertRaisesRegex(ValueError, "wystawionej fakturze"):
            set_tasks_status([task], "W toku", actor_id=1)
        with self.assertRaisesRegex(ValueError, "wystawionej fakturze"):
            save_task("Powrót", status="Nowe", firma_id=self.company, task_id=task, actor_id=1)
        self.assertEqual(self.row(activity)["state"], "done")

    def test_billed_legacy_activity_cannot_be_attached_to_pending_task(self):
        activity = add_company_extra_service(self.company, "2026-09", self.service, 1, 50, True)
        self.issue(activity, legacy=True)
        task = save_task("Nie wykonano", firma_id=self.company, actor_id=1)
        with self.assertRaises(ValueError):
            link_extra_activity_task(activity, task, 1)
        with self.assertRaises(ValueError):
            create_task_for_extra_activity(activity, 1)
        self.assertEqual(self.row(activity)["state"], "unlinked")
        created = create_task_for_extra_activity(activity, 1, status="Zakończone")
        self.assertEqual(self.row(activity)["zadanie_id"], created)

    def test_list_filters_company_month_and_task_state(self):
        done = self.activity(task_status="Zakończone")
        create_extra_activity(self.company, "2026-10", self.service, 1, 50, True,
                              actor_id=1, task_title="Późniejsza")
        self.assertEqual([row["id"] for row in list_extra_activities(1, self.company, "2026-09", "done")], [done])
        self.assertEqual(list_extra_activities(1, self.company, "2026-10", "done"), [])

    def test_invalid_month_or_nonfinite_quantity_is_rejected_without_task_creation(self):
        for month, quantity, price in (("2026-13", 1, 50), ("2026-09", float("nan"), 50), ("2026-09", 1, float("inf"))):
            with self.subTest(month=month, quantity=quantity, price=price), self.assertRaises(ValueError):
                create_extra_activity(self.company, month, self.service, quantity, price, True,
                                      actor_id=1, task_title="Błędna")
        self.assertEqual(list_extra_activities(1), [])

