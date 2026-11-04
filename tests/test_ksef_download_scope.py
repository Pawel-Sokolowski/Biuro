import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QMessageBox

from app.invoices import faktury_ui
from app.invoices.faktury_ui import FakturyPage
from tests.base import DbTestCase


class KsefDownloadScopeTests(DbTestCase):
    def _page(self, firma_id):
        page = FakturyPage.__new__(FakturyPage)
        page._ksef_send_worker = None
        page._selected_company_id = lambda: firma_id
        page._current_period = lambda: "2026-10"
        page._start_ksef_download = mock.Mock()
        return page

    def _run(self, page):
        with mock.patch.object(faktury_ui, "ksef_configured", return_value=True), \
                mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            page._download_from_ksef()

    def test_selected_company_limits_download_to_its_nip(self):
        firma = self.make_company("Firma KSeF", nip="8522605060")
        page = self._page(firma)
        self._run(page)
        page._start_ksef_download.assert_called_once_with("2026-10", buyer_nip="8522605060")

    def test_no_company_downloads_for_all(self):
        page = self._page(None)
        self._run(page)
        page._start_ksef_download.assert_called_once_with("2026-10")

    def test_selected_company_without_nip_is_not_downloaded(self):
        firma = self.make_company("Bez NIP", nip="")
        page = self._page(firma)
        with mock.patch.object(QMessageBox, "warning") as warning:
            self._run(page)
        warning.assert_called_once()
        page._start_ksef_download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
