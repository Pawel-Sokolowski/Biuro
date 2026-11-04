"""A parser failure after a flushed batch must close the workbook and roll back."""
from unittest import mock

from app.db.dao_conn import get_conn
from app.reports import import_dao
from tests.base import DbTestCase
from tests.test_import_parse import WORK_HEADER


class StreamingImportTests(DbTestCase):
    def test_error_after_a_batch_rolls_back_and_closes_the_source(self):
        closed = []
        def rows(path):
            try:
                yield WORK_HEADER
                for number in range(501):
                    yield ('Firma', '1234567890', 'Jan Kowalski', '', '2026-11-04', '08:00', '09:00',
                           '1:00:00', 60, '0:00:00', 0, f'Księgowość {number}', '')
                raise ValueError('malformed worksheet')
            finally:
                closed.append(True)
        with mock.patch.object(import_dao, 'read_sheet_rows', side_effect=rows):
            with self.assertRaisesRegex(ValueError, 'malformed worksheet'):
                import_dao.import_work_file('stream.xlsx')
        self.assertEqual(closed, [True])
        with get_conn() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM import_czas_pracy').fetchone()[0], 0)

    def test_workbook_is_closed_if_dao_fails_mid_stream(self):
        book = mock.Mock()
        book.active.iter_rows.return_value = iter([
            WORK_HEADER, ('Firma', '1234567890', 'Jan Kowalski', '', '2026-11-04', '08:00', '09:00',
                          '1:00:00', 60, '0:00:00', 0, 'Księgowość', '')])
        with mock.patch('openpyxl.load_workbook', return_value=book), \
                mock.patch.object(import_dao, '_match_company', side_effect=RuntimeError('database failure')):
            with self.assertRaises(RuntimeError):
                import_dao.import_work_file('stream.xlsx')
        book.close.assert_called_once()
