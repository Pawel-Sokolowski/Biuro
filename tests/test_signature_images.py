"""Signature images remain visible in the composer and travel as MIME/CID."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PyQt6.QtCore import QMimeData, QUrl
from PyQt6.QtGui import QImage, QColor, QTextDocument
from PyQt6.QtWidgets import QApplication
from app.email.signature_editor import SignatureEditor
from app.email.compose_dialog import ComposeDialog
from app.email.signature import build_default_signature
from app.email.email_actions import prepare_message


class SignatureImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(['signature-test'])

    def setUp(self):
        self.image = QImage(40, 20, QImage.Format.Format_ARGB32)
        self.image.fill(QColor('green'))

    def test_pasted_image_is_embedded_persisted_visible_and_sent_as_cid(self):
        editor = SignatureEditor(lambda: ('{imie}', '{nazwisko}'))
        self.addCleanup(editor.close)
        editor.edit.setHtml('<p>Pozdrawiam,<br>{imie} {nazwisko}<br>{telefon}</p>')
        editor.edit.moveCursor(editor.edit.textCursor().MoveOperation.End)
        mime = QMimeData()
        mime.setImageData(self.image)
        editor.edit.insertFromMimeData(mime)
        text, html = editor.signature()
        self.assertIn('data:image/png;base64,', html)
        text, html = build_default_signature('Anna', 'Nowak', {'stopka_szablon_html':html}, telefon='+48 123')
        with patch('app.email.signature.get_employee_signature', return_value=(text, html)):
            composer = ComposeDialog(pracownik_id=7, body='Dzień dobry')
        self.addCleanup(composer.close)
        self.assertIn('Anna Nowak', composer.body_edit.toPlainText())
        url = html.split('src="', 1)[1].split('"', 1)[0]
        resource = composer.body_edit.document().resource(QTextDocument.ResourceType.ImageResource, QUrl(url))
        self.assertFalse(resource.isNull())
        self.assertEqual(resource.width(), 40)
        values = composer.values()
        self.assertIn('data:image/png;base64,', values['html'])
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / 'invoice.pdf'
            pdf.write_bytes(b'%PDF-test')
            message = prepare_message('office@example.com', 'Office', ['client@example.com'], 'Test', values['body'], attachments=[str(pdf)], html_body=values['html'])
        parts = list(message.walk())
        image_parts = [part for part in parts if part.get_content_type() == 'image/png']
        self.assertEqual(len(image_parts), 1)
        image_part = image_parts[0]
        self.assertEqual(image_part.get_content_disposition(), 'inline')
        self.assertTrue(image_part.get_payload(decode=True).startswith(b'\x89PNG'))
        sent_html = message.get_body(preferencelist=('html',)).get_content()
        self.assertIn('cid:' + image_part['Content-ID'][1:-1], sent_html)
        self.assertNotIn('data:image', sent_html)
        self.assertEqual(len(list(message.iter_attachments())), 1)

    def test_file_image_survives_file_removal(self):
        editor = SignatureEditor(lambda: ('A', 'B'))
        self.addCleanup(editor.close)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'logo.png'
            self.image.save(str(path))
            with patch('app.email.signature_editor.QFileDialog.getOpenFileName', return_value=(str(path), '')):
                editor.add_image()
        self.assertIn('data:image/png;base64,', editor.edit.toHtml())
        self.assertNotIn('file:', editor.edit.toHtml())

    def test_employee_values_are_escaped_in_html(self):
        text, html = build_default_signature('<b>Anna</b>', 'A&B', {'stopka_szablon_html':'<p>{imie} {nazwisko}<br>{telefon}</p>'}, telefon='<script>1</script>')
        self.assertNotIn('<script>', html)
        self.assertNotIn('<b>Anna</b>', html)
        self.assertIn('A&amp;B', html)
