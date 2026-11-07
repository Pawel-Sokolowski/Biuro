"""Transactional failure paths and measured-work invariants from the PR review."""

from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QSignalSpy
from tests.base import DbTestCase


class TimerBillingTests(DbTestCase):
    def test_failed_timer_commit_rolls_back_billing_and_retry_records_once(self):
        from app.db.dao_conn import get_conn
        from app.monthly_view.monthly_view_logic import MonthlyViewLogic
        from app.monthly_view import monthly_view_logic as module

        employee = self.make_employee()
        company = self.make_company()
        with get_conn() as conn:
            service = conn.execute(
                "INSERT INTO uslugi_dodatkowe(nazwa,cena_netto,stawka_vat,kategoria) VALUES('Fixture',100,'23','inne') RETURNING id"
            ).fetchone()[0]
        logic = MonthlyViewLogic(employee)
        logic.set_month("2026-10")
        logic.toggle_stoper(
            company,
            "ksiegowosc",
            {"typ_czynnosci": "extra", "fakturowac": True, "usluga_id": service},
        )
        logic._durable_timers["ksiegowosc"].base_seconds = 3600
        session_id = logic._durable_timers["ksiegowosc"].session_id
        failed = []

        class FailingCommit:
            def __init__(self):
                self.conn = get_conn()

            def __getattr__(self, name):
                return getattr(self.conn, name)

            def commit(self):
                active = self.conn.execute(
                    "SELECT 1 FROM active_timer_sessions WHERE session_id=%s",
                    (session_id,),
                ).fetchone()
                if not active and not failed:
                    failed.append(True)
                    raise RuntimeError("simulated failure before COMMIT")
                self.conn.commit()

        with mock.patch.object(module, "get_conn", side_effect=FailingCommit):
            with self.assertRaisesRegex(RuntimeError, "before COMMIT"):
                logic.stop_company_timers()
        with get_conn() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM uslugi_dodatkowe_firma WHERE firma_id=%s",
                    (company,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM active_timer_sessions WHERE session_id=%s",
                    (session_id,),
                ).fetchone()[0],
                1,
            )
        logic.stop_company_timers()
        with get_conn() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM uslugi_dodatkowe_firma WHERE firma_id=%s",
                    (company,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM active_timer_sessions WHERE session_id=%s",
                    (session_id,),
                ).fetchone()[0],
                0,
            )


class ChatWatermarkTests(DbTestCase):
    def test_delayed_acknowledgement_does_not_read_newer_messages_or_regress(self):
        from app.chat import chat_model as chat
        from app.db.dao_conn import get_conn

        sender = self.make_employee("sender@example.com")
        reader = self.make_employee("reader@example.com")
        conversation = chat.create_conversation("Fixture", sender, [reader])
        with get_conn() as conn:
            conn.execute(
                "UPDATE uczestnicy_chat SET ostatnie_przeczytane_id=0 WHERE konwersacja_id=%s",
                (conversation,),
            )
        first = chat.add_message(conversation, sender, "First")
        second = chat.add_message(conversation, sender, "Second")
        chat.mark_conversation_read(conversation, reader, through_id=first)
        self.assertEqual(chat.get_unread_counts(reader)[conversation], 1)
        chat.mark_conversation_read(conversation, reader, through_id=0)
        self.assertEqual(chat.get_unread_counts(reader)[conversation], 1)
        chat.mark_conversation_read(conversation, reader, through_id=second)
        self.assertNotIn(conversation, chat.get_unread_counts(reader))


class AllocationAndModelTests(TestCase):
    def test_annual_amount_preserves_cents_before_employee_allocation(self):
        from app.reports.import_parse import compute_employee_report

        for amount, discount in (
            (Decimal("100.01"), Decimal(0)),
            (Decimal("100.07"), Decimal(0)),
            (Decimal("100.01"), Decimal("-20.02")),
            (Decimal("100.01"), Decimal("-200")),
        ):
            rows = compute_employee_report(
                [],
                [
                    dict(
                        firma_id=1,
                        kontrahent="Fixture",
                        miesiac="2026-01",
                        rodzaj="ksiegowosc",
                        wartosc_netto=amount,
                    ),
                    dict(
                        firma_id=1,
                        kontrahent="Fixture",
                        miesiac="2026-02",
                        rodzaj="inne_ksiegowosc",
                        wartosc_netto=0,
                    ),
                    dict(
                        firma_id=1,
                        kontrahent="Fixture",
                        miesiac="2026-01",
                        rodzaj="rabat",
                        wartosc_netto=discount,
                    ),
                ],
                {},
            )
            self.assertEqual(
                sum(Decimal(str(row["cena"])) for row in rows), amount + discount
            )

    def test_table_inserts_by_id_and_updates_only_changed_column(self):
        from app.utils.item_table import ItemTable
        from app.utils.table_records import TableRecord

        table = ItemTable(["ID", "Value"])
        self.addCleanup(table.close)
        table.set_records([TableRecord("1", ("1", "a")), TableRecord("2", ("2", "b"))])
        reset, inserted, changed = (
            QSignalSpy(table.model().modelReset),
            QSignalSpy(table.model().rowsInserted),
            QSignalSpy(table.model().dataChanged),
        )
        table.set_records(
            [
                TableRecord("0", ("0", "x")),
                TableRecord("1", ("1", "a")),
                TableRecord("2", ("2", "updated")),
            ]
        )
        self.assertEqual(len(reset), 0)
        self.assertEqual(len(inserted), 1)
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0][0].column(), 1)
        self.assertEqual(table.item(2, 1).text(), "updated")

    def test_explicit_sync_reports_busy_instead_of_success(self):
        from app.email import email_sync as sync

        with (
            mock.patch.object(
                sync, "account_sync_lock", return_value=nullcontext(False)
            ),
            mock.patch.object(sync, "_sync_and_prefetch_impl") as work,
        ):
            with self.assertRaises(sync.MailboxSyncBusy):
                sync.sync_and_prefetch(1, ["INBOX"], first_run=True)
        work.assert_not_called()


class PreparedImportTests(DbTestCase):
    def test_preview_rows_reused_without_reopening_parser_and_source_change_rejected(
        self,
    ):
        from app.utils import importers

        with TemporaryDirectory() as directory:
            path = Path(directory) / "companies.csv"
            path.write_text("Nazwa Firmy;NIP\nFixture;9240000010\n", encoding="utf-8")
            plan, prepared = importers.prepare_company_import(str(path))
            self.addCleanup(prepared.close)
            self.assertEqual(plan["created"], 1)
            with (
                mock.patch.object(
                    importers,
                    "_load_rows_from_file",
                    side_effect=AssertionError("parsed again"),
                ),
                mock.patch.object(importers, "enrich_payload", return_value=None),
                mock.patch.object(importers, "check_ceidg_status_for_company"),
                mock.patch.object(importers, "check_vat_for_company"),
            ):
                result = importers.import_companies_fast_from_excel_file(
                    str(path), prepared=prepared
                )
            self.assertEqual(result["created"], 1)
            path.write_text("Nazwa Firmy;NIP\nChanged;9240000010\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "zmienił"):
                list(prepared.rows(str(path)))


class MailSnapshotTests(DbTestCase):
    def test_accounts_share_four_reads_with_visibility_and_notification_limits(self):
        from app.db.dao_conn import get_conn
        from app.email import email_storage as storage

        with get_conn() as conn:
            for account in (71, 72):
                conn.execute(
                    "INSERT INTO konta_email(id,email) VALUES(%s,%s)",
                    (account, f"{account}@example.com"),
                )
                for folder in ("INBOX", "Hidden", "Sent"):
                    folder_id = conn.execute(
                        "INSERT INTO foldery_email(konto_id,server_name,attributes,delimiter) VALUES(%s,%s,%s,%s) RETURNING id",
                        (account, folder, "\\Sent" if folder == "Sent" else "", "/"),
                    ).fetchone()[0]
                    for i in range(5):
                        conn.execute(
                            "INSERT INTO wiadomosci_email(konto_id,folder_id,uid,temat) VALUES(%s,%s,%s,%s)",
                            (account, folder_id, str(i), folder),
                        )
        calls = []
        real = storage.get_conn

        class Counted:
            def __init__(self):
                self.conn = real()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.conn.__exit__(*args)

            def execute(self, *args, **kwargs):
                calls.append(args[0])
                return self.conn.execute(*args, **kwargs)

        with mock.patch.object(storage, "get_conn", side_effect=Counted):
            result = storage.mail_notification_snapshot(
                [{"id": 71}, {"id": 72}],
                {"71": {"Hidden"}, "72": {"Hidden"}},
                {71: 0, 72: 0},
                limit=2,
            )
        self.assertEqual(len(calls), 5)  # isolation statement + four data queries
        self.assertEqual(result.total, 20)  # badges include visible Sent, unlike toasts
        self.assertEqual(len(result.messages), 4)
        self.assertTrue(all(message["temat"] == "INBOX" for message in result.messages))


class BackgroundViewTests(TestCase):
    def test_chat_hidden_during_read_does_not_render_or_acknowledge(self):
        from types import SimpleNamespace
        from PyQt6.QtWidgets import QListWidget, QListWidgetItem
        from app.chat import chat_ui as chat

        class Capture:
            def submit(self, work, done, error=None):
                self.work, self.done = work, done

        items = QListWidget()
        item = QListWidgetItem("Fixture")
        item.setData(Qt.ItemDataRole.UserRole, 1)
        items.addItem(item)
        page = SimpleNamespace(
            _poll_busy=False,
            chat_conversations=items,
            pracownik_id=1,
            current_konwersacja=1,
            isVisible=lambda: True,
            offset=0,
            page_size=50,
            _seen_ids={1: 10},
            _unread={},
            _reads=Capture(),
            _rendered_messages=[{"id": 10}],
            _rendered_conversation=1,
            _render_grouped_messages=mock.Mock(),
            _mark_read=mock.Mock(),
        )
        chat.ChatPage._poll_async(page)
        page.isVisible = lambda: False
        page._reads.done(({1: {"id": 11}}, {}, [{"id": 11}], {}))
        page._render_grouped_messages.assert_not_called()
        page._mark_read.assert_not_called()
        items.close()

    def test_bulk_generation_is_background_and_duplicate_clicks_are_guarded(self):
        import threading
        import time
        from types import SimpleNamespace
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QWidget, QPushButton, QMessageBox
        from app.invoices.faktury_ui import FakturyPage
        from app.utils.background import CommandRunner
        from tests.base import QT_APP

        page = FakturyPage.__new__(FakturyPage)
        QWidget.__init__(page)
        self.addCleanup(page.close)
        page._generation_busy = False
        page._generation_progress = None
        page._current_month = lambda: "2026-10"
        page.btn_generate_all = QPushButton("Generate", page)
        page._invoice_commands = CommandRunner(page)
        page.refresh_async = mock.Mock()
        page.generation_progress.connect(page._update_generation_progress)
        ready = threading.Event()
        main_thread = threading.get_ident()

        def generate(*args, **kwargs):
            self.assertNotEqual(threading.get_ident(), main_thread)
            self.assertTrue(ready.wait(3))
            kwargs["progress"](1, 1, "Fixture")
            return {"created": 1, "processed": 1}

        page.logic = SimpleNamespace(
            generate_and_save_invoices_for_month=mock.Mock(side_effect=generate)
        )
        QTimer.singleShot(0, ready.set)
        with (
            mock.patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
            ) as confirm,
            mock.patch.object(QMessageBox, "information"),
        ):
            page._on_generate_clicked()
            self.assertTrue(page._generation_busy)
            page._on_generate_clicked()
            self.assertEqual(confirm.call_count, 1)
            deadline = time.monotonic() + 5
            while page._generation_busy and time.monotonic() < deadline:
                QT_APP.processEvents()
            self.assertFalse(page._generation_busy)
        page.logic.generate_and_save_invoices_for_month.assert_called_once()
        page.refresh_async.assert_called_once()
