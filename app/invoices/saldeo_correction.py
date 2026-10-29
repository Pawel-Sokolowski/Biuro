"""Rejestracja korekty wystawionej w Saldeo, bez udawania importu zwykłej faktury."""
from app.db.dao_conn import get_conn
from app.db.dao_invoices import can_access_invoice
from app.security.authorization import can, is_administrator


def confirm_saldeo_correction(invoice_id: int, number: str, actor_id: int) -> None:
    number = (number or '').strip()
    if not number or len(number) > 256:
        raise ValueError('Podaj numer korekty nadany w Saldeo.')
    if not (is_administrator(actor_id) or can(actor_id, 'can_export_data')):
        raise PermissionError('Brak uprawnień do potwierdzenia wystawienia korekty.')
    conn = get_conn()
    try:
        invoice = conn.execute('SELECT * FROM faktury WHERE id = %s FOR UPDATE', (invoice_id,)).fetchone()
        if not invoice or not can_access_invoice(actor_id, invoice['firma_id']):
            raise PermissionError('Brak dostępu do wybranej korekty.')
        if invoice['rodzaj'] != 'korekta' or not invoice['korygowana_faktura_id']:
            raise ValueError('Wybierz korektę powiązaną z fakturą pierwotną.')
        if invoice['eksportowana']:
            if invoice['numer'] == number:
                return
            raise ValueError('Korekta jest już wystawiona. Nie można zmienić jej numeru.')
        if invoice['ksef_numer'] or invoice['ksef_status'] in ('wysylanie', 'oczekuje', 'do_sprawdzenia', 'przyjeta'):
            raise ValueError('Najpierw wyjaśnij rozpoczętą wysyłkę tej korekty do KSeF.')
        original = conn.execute('SELECT eksportowana FROM faktury WHERE id = %s', (invoice['korygowana_faktura_id'],)).fetchone()
        if not original or not original[0]:
            raise ValueError('Faktura pierwotna musi być wystawiona.')
        if conn.execute('SELECT 1 FROM faktury WHERE numer = %s AND id <> %s', (number, invoice_id)).fetchone():
            raise ValueError('Ten numer jest już zapisany przy innym dokumencie.')
        conn.execute("""
            UPDATE faktury SET numer = %s, eksportowana = 1, status = 'wystawiona',
                data_eksportu = to_char(now(), 'YYYY-MM-DD HH24:MI:SS'),
                updated_at = to_char(now(), 'YYYY-MM-DD HH24:MI:SS') WHERE id = %s
        """, (number, invoice_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
