import tempfile
from pathlib import Path
from tests.base import DbTestCase
from tests.test_payment_safety import statement
from app.db.dao_conn import get_conn
from app.db.dao_payments import import_mt940_file, list_unsettled_transfers, bank_import_summary


class SharedBankImportsTests(DbTestCase):
    def test_import_is_visible_to_another_employee_with_the_same_finance_role(self):
        first = self.make_employee('first@example.com')
        second = self.make_employee('second@example.com')
        with get_conn() as conn:
            role = conn.execute("INSERT INTO role(nazwa,can_view_finances) VALUES ('Finance test',1) RETURNING id").fetchone()[0]
            conn.execute('UPDATE pracownicy SET rola_id=%s WHERE id=ANY(%s)', (role, [first, second]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bank.sta'
            path.write_bytes(statement())
            self.assertEqual(import_mt940_file(str(path), first)['new'], 1)
        self.assertEqual(list_unsettled_transfers(first), list_unsettled_transfers(second))
        self.assertEqual(len(list_unsettled_transfers(second)), 1)
        self.assertEqual(bank_import_summary(first), bank_import_summary(second))
        self.assertEqual(bank_import_summary(second)['total'], 1)

    def test_inventory_remains_visible_when_no_unsettled_pln_credit_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bank.sta'
            path.write_bytes(statement(currency='EUR'))
            import_mt940_file(str(path), 1)
        self.assertEqual(list_unsettled_transfers(1), [])
        self.assertEqual(bank_import_summary(1)['total'], 1)

    def test_shared_inventory_includes_operations_hidden_from_settlement_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bank.sta'
            path.write_bytes(statement(currency='EUR') + statement('2', direction='D'))
            import_mt940_file(str(path), 1)
        self.assertEqual(list_unsettled_transfers(1), [])
        self.assertEqual(len(list_unsettled_transfers(1, include_all=True)), 2)
        self.assertEqual(len(list_unsettled_transfers(1, include_all=True, limit=1)), 1)
