"""PDF content recognition and real filenames, including hidden Windows extensions."""
from email.message import EmailMessage
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from app.email.email_helpers import attachment_name,get_attachments,pdf_save_path

PDF = b'%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n'


def message(filename='Faktura',content=PDF,subtype='octet-stream'):
    result = EmailMessage()
    result.set_content('Faktura w zalaczeniu')
    kwargs = {'filename':filename} if filename is not None else {}
    result.add_attachment(content,maintype='application',subtype=subtype,**kwargs)
    return result


class PdfAttachmentTests(unittest.TestCase):
    def test_full_filename_is_used_and_never_gets_a_duplicate_pdf_extension(self):
        for name in ('Faktura.pdf','Faktura.PDF','Faktura.2026.pdf'):
            self.assertEqual(attachment_name(name,PDF),name)
            self.assertEqual(get_attachments(message(name))[0]['filename'],name)
            self.assertEqual(pdf_save_path('/tmp/'+name,PDF),'/tmp/'+name)

    def test_real_missing_extension_gets_pdf_even_with_generic_mime(self):
        self.assertEqual(get_attachments(message())[0]['filename'],'Faktura.pdf')
        self.assertEqual(attachment_name(None,PDF),'zalacznik.pdf')
        self.assertEqual(pdf_save_path('/tmp/Faktura',PDF),'/tmp/Faktura.pdf')
        self.assertEqual(attachment_name('Faktura.2026',PDF),'Faktura.2026.pdf')

    def test_mime_alone_cannot_turn_non_pdf_content_into_a_pdf(self):
        self.assertEqual(attachment_name('plik',b'MZ executable'),'plik')
        from app.email.email_ui import EmailPage
        part = list(message('plik.pdf',b'MZ executable','pdf').iter_attachments())[0]
        self.assertFalse(EmailPage._is_safe_pdf_part(part))
        for name in ('plik.exe','plik.pdf.exe','plik.js.pdf'):
            part = list(message(name).iter_attachments())[0]
            self.assertFalse(EmailPage._is_safe_pdf_part(part))
            self.assertEqual(attachment_name(name,PDF),name)

    def test_extensionless_pdf_opens_in_a_pdf_viewer_using_a_fixed_pdf_name(self):
        from app.email import email_ui
        part = list(message().iter_attachments())[0]
        page = SimpleNamespace(_is_safe_pdf_part=email_ui.EmailPage._is_safe_pdf_part)
        page._write_attachment=lambda part,path: email_ui.EmailPage._write_attachment(page,part,path)
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(email_ui.tempfile,'mkdtemp',return_value=directory), \
                mock.patch.object(email_ui.QTimer,'singleShot'), \
                mock.patch.object(email_ui.QDesktopServices,'openUrl',return_value=True) as opened:
            self.assertTrue(email_ui.EmailPage._open_attachment_externally(page,part))
            path = opened.call_args.args[0].toLocalFile()
            self.assertEqual(Path(path).name,'zalacznik.pdf')
            self.assertEqual(Path(path).read_bytes(),PDF)

    def test_save_one_and_save_all_use_content_and_preserve_identical_names(self):
        from app.email import email_ui
        parts = [list(message(name).iter_attachments())[0] for name in ('Faktura','Faktura.pdf')]
        page = SimpleNamespace(_attachment_parts=lambda:parts,_last_save_dir=lambda:'',_remember_save_dir=lambda _:None)
        page._write_attachment=lambda part,path: email_ui.EmailPage._write_attachment(page,part,path)
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(email_ui.QMessageBox,'information'), \
                mock.patch.object(email_ui.QMessageBox,'warning'):
            target = str(Path(directory)/'Wybrana')
            with mock.patch.object(email_ui.QFileDialog,'getSaveFileName',return_value=(target,'')) as chooser:
                email_ui.EmailPage.save_attachments(page,[0])
                self.assertEqual(Path(target+'.pdf').read_bytes(),PDF)
                self.assertTrue(chooser.call_args.args[2].endswith('Faktura.pdf'))
            with mock.patch.object(email_ui.QFileDialog,'getExistingDirectory',return_value=directory):
                email_ui.EmailPage.save_attachments(page,[0,1])
                self.assertEqual((Path(directory)/'Faktura.pdf').read_bytes(),PDF)
                self.assertEqual((Path(directory)/'Faktura (1).pdf').read_bytes(),PDF)
                self.assertFalse((Path(directory)/'Faktura.pdf.pdf').exists())

    def test_legacy_download_and_forward_also_keep_the_correct_full_name(self):
        from app.email import email_actions,email_ui
        for name in ('Faktura','Faktura.pdf'):
            msg = message(name)
            with tempfile.TemporaryDirectory() as directory, \
                    mock.patch.object(email_actions,'load_message',return_value=msg), \
                    mock.patch.object(email_actions,'attachment_output_path',return_value=str(Path(directory)/'saved.pdf')) as output:
                result = email_actions.open_attachment(1,'INBOX','1',0)
                self.assertEqual(Path(result).read_bytes(),PDF)
                output.assert_called_once_with('Faktura.pdf')
            with tempfile.TemporaryDirectory() as directory, \
                    mock.patch.object(email_ui.tempfile,'mkdtemp',return_value=directory):
                forwarded = email_ui.EmailPage._save_attachments_to_temp(msg)
                self.assertEqual(Path(forwarded[0]).name,'Faktura.pdf')
                self.assertEqual(Path(forwarded[0]).read_bytes(),PDF)
