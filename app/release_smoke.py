"""Noninteractive installed-EXE checks; never sends mail or calls external APIs."""
import json
import logging
import os
import sys
from pathlib import Path
import tempfile


def run():
    from app.version import __version__
    from app.config import get_data_dir
    from app.db.schema import schema_is_current
    from app.documents.contract_pack import TEMPLATES, fill_template
    from app.documents import upl_paper, pel_paper, pel_z
    from app.documents.print_renderer import paint_overlay
    from app.invoices.fa3_builder import _schema
    from app.invoices.invoice_preview import write_invoice_pdf
    from pypdf import PdfReader
    output = Path(os.environ.get('BIO_SMOKE_OUTPUT') or get_data_dir() / 'logs' / 'release-smoke.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    result = {'version': __version__, 'checks': []}
    try:
        if sys.platform == 'win32':
            from app.integrations.enova.odbc_support import load_odbc
            load_odbc()
            result['checks'].append('enova_odbc_module')
        if not schema_is_current():
            raise RuntimeError('Database migration is required')
        result['checks'].append('schema')
        _schema()
        result['checks'].append('offline_fa3')
        for module, pages in ((upl_paper, 2), (pel_paper, 3), (pel_z, 3)):
            if len(PdfReader(module.TEMPLATE).pages) != pages:
                raise RuntimeError('Missing or invalid bundled form: ' + module.TEMPLATE.name)
        result['checks'].append('government_templates')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            fill_template('00_framework', {'name': 'Żółć & Example', 'agreement_date': '06.11.2026'}, path / 'contract.docx')
            result['checks'].append('docxtpl')
            write_invoice_pdf(path / 'invoice.pdf', '<html><body><p style="font-size:12pt">Próba PDF — 123,45 zł</p></body></html>')
            if not PdfReader(path / 'invoice.pdf').pages:
                raise RuntimeError('Invoice PDF is empty')
            paint_overlay([{'size': (595,842), 'text': [(50,700,'Próba pełnomocnictwa',400)]}], path / 'overlay.pdf')
            if not PdfReader(path / 'overlay.pdf').pages:
                raise RuntimeError('Overlay PDF is empty')
            result['checks'].append('qt_vector_pdf')
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from stdnum.pl import nip
        if not nip.is_valid('9240000010') or not Credentials or not InstalledAppFlow or not TEMPLATES.is_dir():
            raise RuntimeError('Missing bundled dependencies')
        if os.name == 'nt':
            from winrt.windows.ui.notifications import ToastNotificationManager
            if not ToastNotificationManager:
                raise RuntimeError('WinRT import failed')
            result['checks'].append('winrt')
        result['ok'] = True
        return 0
    except Exception:
        result['ok'] = False
        logging.getLogger(__name__).exception('release_smoke failed')
        return 1
    finally:
        output.write_text(json.dumps(result, indent=2), encoding='utf-8')
