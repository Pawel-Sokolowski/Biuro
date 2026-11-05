"""Explicitly queued PDF delivery, only after confirmed KSeF acceptance."""
import json
import base64
import hashlib
import os
from pathlib import Path
import re
import tempfile
from app.db.dao_conn import get_conn
from app.invoices.ksef_client import KsefError


def parse_recipients(value):
    addresses = list(dict.fromkeys(part.strip() for part in re.split(r'[,;]', str(value or '')) if part.strip()))
    if not addresses or len(addresses) > 10 or any(not re.fullmatch(r'[^\s<>@,;\r\n]+@[^\s<>@,;\r\n]+\.[^\s<>@,;\r\n]+', address) or len(address) > 254 for address in addresses):
        raise ValueError('Podaj poprawny adres e-mail do faktur; kilka adresów oddziel przecinkiem (maks. 10).')
    return addresses


def delivery_plan(invoice_ids, actor_id):
    from app.invoices.ksef_submission import _authorized_invoice
    from app.db.dao_companies import get_company
    from app.db.dao_kontrahenci import get_kontrahent
    result = []
    for invoice_id in invoice_ids:
        invoice = _authorized_invoice(invoice_id, actor_id)
        buyer = get_company(invoice['firma_id']) if invoice.get('firma_id') else get_kontrahent(invoice['kontrahent_id']) if invoice.get('kontrahent_id') else {}
        buyer = buyer or {}
        address = buyer.get('email_faktury') or invoice.get('nabywca_email') or buyer.get('adres_email') or buyer.get('email')
        try:
            recipients = parse_recipients(address)
        except ValueError as exc:
            raise ValueError(f"Faktura {invoice.get('numer') or invoice_id}: {exc}") from exc
        result.append({'id':invoice_id, 'number':invoice.get('numer') or 'Robocza — numer zostanie nadany przy wystawieniu', 'recipients':recipients})
    return result


def queue_deliveries(plans, actor_id, account_id):
    from app.email.email_storage import get_email_accounts_for_employee
    from app.invoices.ksef_submission import _authorized_invoice
    if account_id not in {account['id'] for account in get_email_accounts_for_employee(actor_id)}:
        raise PermissionError('Nie masz dostępu do wybranego konta nadawcy.')
    for plan in plans:
        _authorized_invoice(plan['id'], actor_id)
        parse_recipients(','.join(plan['recipients']))
    conn = get_conn()
    try:
        for plan in plans:
            row = conn.execute('SELECT invoice_email_status, invoice_email_recipients, invoice_email_account_id FROM faktury WHERE id=%s FOR UPDATE', (plan['id'],)).fetchone()
            if not row:
                raise ValueError('Faktura nie istnieje.')
            encoded = json.dumps(plan['recipients'], ensure_ascii=False)
            if row[0] in ('queued', 'preparing') and row[1] == encoded and row[2] == account_id:
                continue
            if row[0] not in (None, '', 'failed_before_send'):
                raise ValueError(f"Faktura {plan['number']}: e-mail jest już w kolejce, został wysłany albo wymaga sprawdzenia. Nie wysyłam drugi raz.")
            conn.execute('''UPDATE faktury SET invoice_email_status='queued', invoice_email_recipients=%s,
                            invoice_email_account_id=%s, invoice_email_actor_id=%s, invoice_email_error=NULL WHERE id=%s''',
                         (encoded,account_id,actor_id,plan['id']))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def official_pdf(invoice, xml_path):
    from app.invoices.ksef_sync import parse_fa3_invoice
    from app.invoices.invoice_preview import _qr_data_url, invoice_html, write_invoice_pdf
    raw = Path(xml_path).read_bytes()
    data = parse_fa3_invoice(raw.decode('utf-8-sig'), allow_buyer_without_nip=True)
    official = {**invoice, '_official':True, 'ksef_nip_wysylki':data['seller_nip'],
                'ksef_hash_faktury':base64.b64encode(hashlib.sha256(raw).digest()).decode(), 'numer':data['number'], 'data_wystawienia':data['issue_date'],
                'data_sprzedazy':data['sale_date'], 'termin_platnosci':data['due_date'],
                'nabywca_pelna':data['buyer_name'], 'nabywca_nip':data['buyer_nip'],
                'nabywca_adres':' '.join(filter(None,(data['buyer_address'],data['buyer_address_2']))),
                'nabywca_kod':'','nabywca_miasto':'','nabywca_kraj':data['buyer_country'],
                'sprzedawca_nazwa':data['seller_name'], 'sprzedawca_nip':data['seller_nip'],
                'sprzedawca_adres':data['seller_address'], 'konto_bankowe':data['account'],
                'kwota_netto':data['net'], 'kwota_brutto':data['gross'],
                'rodzaj':'korekta' if data['kind'] in ('KOR','KOR_ZAL','KOR_ROZ') else 'faktura',
                'przyczyna_korekty':data['correction_reason'],
                'korygowana_faktura_numer':data['corrected_number']}
    official['_qr_data_url'] = _qr_data_url(official)
    if not official.get('ksef_numer') or not official['_qr_data_url']:
        raise KsefError('PDF do wysyłki wymaga numeru KSeF i poprawnie wygenerowanego kodu QR.')
    destination = Path(xml_path).with_suffix('.pdf')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.pdf', delete=False) as handle:
            temporary = Path(handle.name)
        write_invoice_pdf(temporary, invoice_html(official,data['positions']), title='Faktura '+data['number'])
        if not temporary.read_bytes().startswith(b'%PDF'):
            raise OSError('Nie udało się wygenerować PDF faktury.')
        os.replace(temporary,destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return str(destination)


def _delivery_state(invoice_id, status, *, error=None, pdf=None, expected=None):
    conn = get_conn()
    try:
        predicate = ''
        snapshot = ()
        if expected is not None:
            predicate = ''' AND invoice_email_recipients IS NOT DISTINCT FROM %s
                            AND invoice_email_account_id IS NOT DISTINCT FROM %s
                            AND invoice_email_actor_id IS NOT DISTINCT FROM %s'''
            snapshot = (expected.get('invoice_email_recipients'), expected.get('invoice_email_account_id'),
                        expected.get('invoice_email_actor_id'))
        conn.execute('''UPDATE faktury SET invoice_email_status=%s, invoice_email_error=%s,
                        invoice_email_pdf_path=COALESCE(%s,invoice_email_pdf_path),
                        invoice_email_sent_at=CASE WHEN %s='sent' THEN now() ELSE invoice_email_sent_at END
                        WHERE id=%s AND (%s <> 'failed_before_send'
                        OR COALESCE(invoice_email_status,'') IN ('queued','preparing','failed_before_send'))''' + predicate,
                     (status,error,pdf,status,invoice_id,status) + snapshot)
        conn.commit()
    finally:
        conn.close()


def deliver_if_queued(invoice_id, actor_id, xml_path):
    from app.invoices.ksef_submission import _authorized_invoice
    from app.email.email_storage import get_email_accounts_for_employee
    from app.email.email_actions import get_smtp_config_for_account, send_email_smtp
    invoice = _authorized_invoice(invoice_id,actor_id)
    status = invoice.get('invoice_email_status')
    if not status:
        return None
    if status == 'sent':
        return {'status':'sent','recipients':invoice.get('invoice_email_recipients')}
    if status in ('sending','uncertain'):
        return {'status':'uncertain','error':'Wynik poprzedniej wysyłki SMTP jest niepewny. Sprawdź folder Wysłane przed ponowieniem.'}
    if invoice.get('ksef_status') != 'przyjeta' or not invoice.get('ksef_numer'):
        raise KsefError('PDF można wysłać dopiero po przyjęciu faktury w KSeF.')
    try:
        recipients = json.loads(invoice.get('invoice_email_recipients') or '[]')
        parse_recipients(','.join(recipients))
        account_id = invoice.get('invoice_email_account_id')
        if account_id not in {account['id'] for account in get_email_accounts_for_employee(actor_id)}:
            raise PermissionError('Brak dostępu do konta nadawcy zapisanego w kolejce.')
        config = get_smtp_config_for_account(account_id)
        if not config.get('host') or not config.get('user'):
            raise ValueError('Uzupełnij konfigurację SMTP wybranego nadawcy.')
        pdf = official_pdf(invoice,xml_path)
    except Exception as exc:
        _delivery_state(invoice_id,'failed_before_send',error=str(exc),expected=invoice)
        raise
    # Only the process that atomically reserves this row may enter SMTP.
    conn = get_conn()
    try:
        reserved = conn.execute('''UPDATE faktury SET invoice_email_status='sending', invoice_email_error=NULL,
                                   invoice_email_pdf_path=%s WHERE id=%s
                                   AND invoice_email_status IN ('queued','preparing','failed_before_send')
                                   AND invoice_email_recipients IS NOT DISTINCT FROM %s
                                   AND invoice_email_account_id IS NOT DISTINCT FROM %s
                                   AND invoice_email_actor_id IS NOT DISTINCT FROM %s RETURNING id''',
                                (pdf,invoice_id,invoice.get('invoice_email_recipients'),account_id,invoice.get('invoice_email_actor_id'))).fetchone()
        conn.commit()
    finally:
        conn.close()
    if not reserved:
        return {'status':'uncertain','error':'Inny proces obsłużył tę wysyłkę. Nie wysyłam ponownie.'}
    try:
        ok, info = send_email_smtp(account_id,recipients,'Faktura '+str(invoice['numer']),
                                  f"Dzień dobry,\n\nw załączeniu faktura {invoice['numer']}.\nNumer KSeF: {invoice['ksef_numer']}\n\nPozdrawiamy",
                                  attachments=[pdf],save_copy=True,require_attachments=True)
    except Exception as exc:
        ok, info = False, str(exc)
    if not ok:
        _delivery_state(invoice_id,'uncertain',error=info,pdf=pdf)
        return {'status':'uncertain','error':info}
    _delivery_state(invoice_id,'sent',pdf=pdf)
    return {'status':'sent','recipients':recipients,'path':pdf}


def review_delivery(parent, invoice_ids, actor_id):
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
                                 QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QHeaderView)
    from app.email.email_storage import get_email_accounts_for_employee
    plans = delivery_plan(invoice_ids,actor_id)
    accounts = get_email_accounts_for_employee(actor_id)
    if not accounts:
        raise ValueError('Nie masz przypisanego konta pocztowego do wysyłki faktur.')
    dialog = QDialog(parent)
    dialog.setWindowTitle('Wysyłka faktur PDF do klientów')
    dialog.resize(750,420)
    layout = QVBoxLayout(dialog)
    info = QLabel('PDF zostanie wygenerowany i wysłany dopiero po przyjęciu faktury w KSeF, z numerem i kodem QR. Sprawdź nadawcę i odbiorców.')
    info.setWordWrap(True);layout.addWidget(info)
    settings = QSettings('Biuro','OfficeManager')
    sender = QComboBox()
    for account in accounts:
        sender.addItem(account['email'],account['id'])
    key = f'users/{actor_id}/invoice_sender'
    sender.setCurrentIndex(max(0,sender.findData(settings.value(key,None))))
    form = QFormLayout();form.addRow('Nadawca:',sender);layout.addLayout(form)
    table = QTableWidget(len(plans),2)
    table.setHorizontalHeaderLabels(['Faktura','Adres e-mail do faktur'])
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    for row,plan in enumerate(plans):
        table.setItem(row,0,QTableWidgetItem(plan['number']))
        table.setItem(row,1,QTableWidgetItem(', '.join(plan['recipients'])))
    layout.addWidget(table)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Potwierdź wysyłkę PDF')
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Anuluj')
    buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    settings.setValue(key,sender.currentData())
    return plans,sender.currentData()
