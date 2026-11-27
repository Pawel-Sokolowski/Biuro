import json
import unittest
from pathlib import Path

import psycopg

from app.db.dao_conn import get_conn

from tests.base import DbTestCase


class AlembicDeploymentTests(unittest.TestCase):
    def test_invoice_description_upgrade_repairs_existing_draft_saves(self):
        from alembic import command
        from alembic.config import Config
        from app.config import get_schema
        from app.db.dao_companies import save_company
        from app.db.dao_invoices import create_manual_invoice, get_invoice_by_id
        from app.db.dao_kontrahenci import save_kontrahent
        from app.db.schema import schema_is_current, upgrade_database
        from tests.base import create_test_schema, drop_test_schema

        previous = get_schema()
        schema = create_test_schema()
        try:
            config = Config()
            config.set_main_option('script_location', str(Path(__file__).resolve().parents[1] / 'app/db/migrations'))
            command.upgrade(config, '20261127_01')
            with get_conn() as conn:
                # Fresh bootstraps already have this column. Installed databases may not.
                conn.execute('ALTER TABLE faktury DROP COLUMN IF EXISTS opis_dodatkowy')
            self.assertFalse(schema_is_current())
            upgrade_database()
            self.assertTrue(schema_is_current())
            buyers = [
                ('firma', save_company({'nazwa': 'Firma testowa'})),
                ('kontrahent', save_kontrahent({'typ': 'osoba_fizyczna', 'nazwa': 'Jan Testowy'})),
            ]
            for kind, buyer_id in buyers:
                with self.subTest(kind=kind):
                    invoice_id = create_manual_invoice(
                        kind, buyer_id, [{'nazwa': 'Usługa', 'ilosc': 1, 'cena_netto': 10, 'vat': 23}],
                        '2026-11-27', '2026-11-27', '2026-12-04', opis_dodatkowy='Opis testowy',
                    )
                    invoice = get_invoice_by_id(invoice_id)
                    self.assertEqual(invoice['status'], 'robocza')
                    self.assertEqual(invoice['opis_dodatkowy'], 'Opis testowy')
        finally:
            drop_test_schema(schema, previous)

    def test_fresh_upgrade_provisions_bulk_history_before_first_campaign(self):
        from unittest.mock import patch
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
        from app.config import get_dsn, get_schema
        from app.db.schema import schema_is_current, upgrade_database
        from app.services.bulk_mail_log import history
        from tests.base import create_test_schema, drop_test_schema

        previous = get_schema()
        schema = create_test_schema()
        try:
            upgrade_database()
            keyword_dsn = make_conninfo(**conninfo_to_dict(get_dsn()))
            with patch('app.config.get_dsn', return_value=keyword_dsn):
                upgrade_database()
            self.assertTrue(schema_is_current())
            with get_conn() as conn:
                tables = {row[0] for row in conn.execute(
                    'SELECT tablename FROM pg_tables WHERE schemaname=%s', (schema,))}
                indexes = {row[0] for row in conn.execute(
                    'SELECT indexname FROM pg_indexes WHERE schemaname=%s', (schema,))}
            self.assertTrue({'bulk_mail_runs', 'bulk_mail_batches'} <= tables)
            self.assertIn('platnik_approved_links', tables)
            self.assertTrue({'idx_bank_transakcje_account', 'idx_bank_transakcje_order', 'idx_bank_transakcje_receipts',
                             'idx_faktury_miesiac_id', 'idx_firmy_normalized_nip'} <= indexes)
            self.assertTrue({'idx_company_contacts_firma_id', 'idx_udzialowcy_firma_id',
                             'idx_faktury_kosz_active_invoice'} <= indexes)
            self.assertTrue({'idx_faktury_bank_reference', 'idx_enova_stage_requests_retry'} <= indexes)
            self.assertEqual(history(), [])
        finally:
            drop_test_schema(schema, previous)

    def test_checking_upgrade_preserves_legacy_history_and_pending_requests(self):
        from alembic import command
        from alembic.config import Config
        from app.config import get_schema
        from app.db.checking_storage import read_snapshot
        from app.db.schema import schema_is_current, upgrade_database
        from app.db.service_tables_schema import apply_checking_queue
        from app.integrations.platnik.declarations import decoded_snapshot
        from app.services.checking_refresh import purge_finished, recover_interrupted
        from app.utils.crypto import encrypt_password
        from tests.base import create_test_schema, drop_test_schema

        previous = get_schema()
        schema = create_test_schema()
        try:
            config = Config()
            config.set_main_option('script_location', str(Path(__file__).resolve().parents[1] / 'app/db/migrations'))
            command.upgrade(config, '20261121_01')
            self.assertFalse(schema_is_current())
            period = '2026-01'
            request_id = 'a' * 32
            timestamp = '2020-02-01T08:00:00+01:00'
            jpk = {'status': 'accepted', 'sent_at': '2026-02-01', 'upo_at': '2026-02-02', 'upo': 'ref'}
            obligations = {'periods': {period: {'vat_period': 'quarterly', 'forms': {'CIT': 'quarterly'}}},
                           'imported': {period: {'forms': {'RCA': 'monthly'}}}}
            platnik = {'declarations': [{'form': 'ZUS RCA'}]}
            encrypted = encrypt_password(json.dumps(platnik))
            with get_conn() as conn:
                company_id = conn.execute("INSERT INTO firmy(nazwa,status) VALUES ('Historia','Archiwalna') RETURNING id").fetchone()[0]
                request = {'id': request_id, 'actor_id': 1, 'kind': 'enova', 'company_ids': [company_id],
                           'period': period, 'state': 'running', 'created_at': timestamp, 'attempts': 2}
                snapshots = {
                    ('enova_checking', period): {'documents': {'accounting': {'AMO': 8}}},
                    ('enova_snapshot', period): {'total': 8},
                    ('enova_jpk', period): jpk,
                    ('enova_hr', ''): {'events': []},
                    ('ezla_sync', ''): {'state': 'downloaded'},
                    ('checking_obligations', ''): obligations,
                    ('platnik_declarations', period): encrypted,
                    ('platnik_declarations', '2025'): encrypted,
                }
                with conn.cursor() as cur:
                    cur.executemany(
                        'INSERT INTO app_settings(klucz,wartosc) VALUES (%s,%s)',
                        [
                            (f'{source}_{company_id}' + (
                                f'_year_{month}' if len(month) == 4 else f'_{month}' if month else ''),
                             payload if source == 'platnik_declarations' else json.dumps(payload))
                            for (source, month), payload in snapshots.items()
                        ] + [
                            (f'checking_refresh_{request_id}', json.dumps(request)),
                            (f'enova_stage_pending_{company_id}_{period}', json.dumps(
                                {'company_id': company_id, 'month': period, 'revision': 'revision-1'})),
                            (f'enova_checking_checked_{company_id}', 'signature'),
                        ],
                    )
                # The prior bootstrap could already have an empty queue table.
                # Seed a completed row to also exercise its column conversion.
                apply_checking_queue(conn)
                conn.execute("""
                    INSERT INTO checking_requests(id,actor_id,kind,company_ids,period,state,created_at,finished_at,results)
                    VALUES ('old',1,'declaration_year',%s,'2025','completed',%s,%s,'[{"ok": true}]')
                """, (json.dumps([company_id]), timestamp, timestamp))
            upgrade_database()
            upgrade_database()
            self.assertTrue(schema_is_current())
            with get_conn() as conn:
                for (source, month), payload in snapshots.items():
                    self.assertEqual(read_snapshot(conn, source, company_id, month), payload)
                self.assertEqual(decoded_snapshot(read_snapshot(conn, 'platnik_declarations', company_id, period)), platnik)
                self.assertEqual(conn.execute('SELECT payload FROM checking_requests WHERE id=%s', (request_id,)).fetchone()[0],
                                 request)
                old = conn.execute("SELECT payload,company_ids FROM checking_requests WHERE id='old'").fetchone()
                self.assertEqual(old['company_ids'], [company_id])
                self.assertEqual(old['payload']['period'], 2025)
                self.assertEqual(old['payload']['results'], [{'ok': True}])
                self.assertEqual(tuple(conn.execute('SELECT company_id,period,revision FROM enova_stage_requests').fetchone()),
                                 (company_id, period, 'revision-1'))
                legacy = conn.execute("""
                    SELECT wartosc FROM app_settings WHERE klucz ~
                      '^(enova_(checking|snapshot|jpk|hr)|ezla_sync|checking_(obligations|refresh)|platnik_declarations|enova_stage_pending)_'
                """).fetchall()
                self.assertEqual([row[0] for row in legacy], ['signature'])
            recover_interrupted()
            self.assertEqual(purge_finished(), 1)
            with get_conn() as conn:
                row = conn.execute('SELECT state,payload FROM checking_requests').fetchone()
                self.assertEqual((row['state'], row['payload']['state'], row['payload']['attempts']), ('queued', 'queued', 2))
        finally:
            drop_test_schema(schema, previous)


class SchemaTests(DbTestCase):
    def tables(self):
        conn = get_conn()
        try:
            return {r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")}
        finally:
            conn.close()

    def test_required_tables_exist(self):
        for name in ("firmy", "pracownicy", "pelnomocnictwa", "wydarzenia", "wydarzenia_dostep",
                     "statusy_miesieczne", "zadania", "czasomierze", "udzialowcy", "app_settings"):
            self.assertIn(name, self.tables())

    def test_init_db_is_idempotent(self):
        from app.db.schema import init_db
        init_db()
        init_db()

    def test_deleted_demo_company_is_not_recreated(self):
        from app.db.schema import init_db

        conn = get_conn()
        try:
            conn.execute("DELETE FROM firmy WHERE nazwa = 'Domyślna firma'")
            conn.commit()
        finally:
            conn.close()

        init_db()

        conn = get_conn()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM firmy WHERE nazwa = 'Domyślna firma'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 0)

    def test_monthly_status_columns_and_check(self):
        conn = get_conn()
        try:
            cols = {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'statusy_miesieczne'")}
            self.assertIn("dokumenty_dostarczone", cols)
            firma = self.make_company()
            with self.assertRaises(psycopg.IntegrityError):
                conn.execute(
                    "INSERT INTO statusy_miesieczne (firma_id, miesiac, etap) VALUES (%s, '2026-01', 'Nieznany')",
                    (firma,),
                )
        finally:
            conn.close()

    def test_pelnomocnictwa_type_is_restricted(self):
        firma = self.make_company()
        emp = self.make_employee()
        conn = get_conn()
        try:
            with self.assertRaises(psycopg.IntegrityError):
                conn.execute(
                    "INSERT INTO pelnomocnictwa (firma_id, pracownik_id, typ) VALUES (%s, %s, 'XYZ')",
                    (firma, emp),
                )
        finally:
            conn.close()

    def test_ksef_invoice_month_is_repaired_from_issue_date(self):
        from app.db.schema import init_db
        firma = self.make_company()
        conn = get_conn()
        try:
            invoice_id = conn.execute(
                "INSERT INTO faktury (firma_id, miesiac, reczna, rodzaj, data_wystawienia, ksef_numer, "
                "kwota_netto, kwota_brutto, pozycje_json) "
                "VALUES (%s, '2026-09', 0, 'faktura', '2026-10-28', 'KSEF-TEST', 0, 0, '[]') RETURNING id",
                (firma,),
            ).fetchone()[0]
            conn.commit()
        finally:
            conn.close()
        init_db()
        conn = get_conn()
        try:
            row = conn.execute("SELECT miesiac, reczna FROM faktury WHERE id=%s", (invoice_id,)).fetchone()
        finally:
            conn.close()
        self.assertEqual((row["miesiac"], row["reczna"]), ("2026-10", 1))


if __name__ == "__main__":
    unittest.main()
