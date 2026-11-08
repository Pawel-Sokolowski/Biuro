"""Seller snapshots and offline access to accepted invoice identity."""
import base64
import hashlib
import re

from psycopg.types.json import Jsonb


def snapshot_seller(conn):
    settings = {r[0]: r[1] for r in conn.execute('SELECT klucz, wartosc FROM app_settings').fetchall()}
    return {
        'sprzedawca_nazwa': next((settings.get(k) for k in ('faktury_sprzedawca_nazwa', 'wystawca_faktur', 'wystawca', 'faktury_wystawca') if settings.get(k)), 'Biuro rachunkowe'),
        'sprzedawca_nip': settings.get('faktury_sprzedawca_nip') or settings.get('ksef_nip') or '',
        'sprzedawca_adres': ' '.join(str(settings.get(k) or '') for k in ('faktury_sprzedawca_adres', 'faktury_sprzedawca_kod', 'faktury_sprzedawca_miasto')).strip(),
        **{'sprzedawca_' + k: settings.get('faktury_sprzedawca_' + k) or '' for k in ('regon', 'email', 'kraj')},
    }


def freeze_seller(conn, invoice_ids):
    if invoice_ids:
        conn.execute('UPDATE faktury SET seller_snapshot=%s WHERE id=ANY(%s) AND seller_snapshot IS NULL AND COALESCE(eksportowana,0)=0',
                     (Jsonb(snapshot_seller(conn)), list(invoice_ids)))


def with_seller_identity(invoice):
    """Never rebuild an issued seller from mutable settings; no network access."""
    result = dict(invoice)
    if result.get('_official'):
        result['_seller_frozen'] = True
        return result
    snapshot = result.get('seller_snapshot')
    if isinstance(snapshot, dict):
        result.update(snapshot)
        result['_seller_frozen'] = True
        return result
    number = str(result.get('ksef_numer') or '')
    if not (result.get('eksportowana') or number):
        return result
    # Legacy accepted documents may already be archived on this workstation.
    # Validate the archive against the stored submission hash before trusting it.
    nip = str(result.get('ksef_nip_wysylki') or '')
    environment = result.get('ksef_srodowisko_wysylki')
    if re.fullmatch(r'[A-Za-z0-9-]{1,100}', number) and re.fullmatch(r'[0-9]{10}', nip) and environment in ('prod', 'test', 'demo'):
        from app.config import get_data_dir
        path = get_data_dir() / 'ksef' / environment / nip / (number + '.xml')
        if path.is_file():
            from app.invoices.ksef_sync import parse_fa3_invoice
            raw = path.read_bytes()
            digest = base64.b64encode(hashlib.sha256(raw).digest()).decode()
            if result.get('ksef_hash_faktury') and digest == result['ksef_hash_faktury']:
                data = parse_fa3_invoice(raw.decode('utf-8-sig'), allow_buyer_without_nip=True)
                if data['number'] == result.get('numer') and data['seller_nip'] == nip:
                    result.update(sprzedawca_nazwa=data['seller_name'], sprzedawca_nip=data['seller_nip'],
                                  sprzedawca_adres=data['seller_address'], sprzedawca_kraj=data['seller_country'],
                                  sprzedawca_email=data['seller_email'], sprzedawca_regon='')
    result.setdefault('sprzedawca_nip', nip)
    result.setdefault('sprzedawca_nazwa', result.get('wystawca') or '')
    result['_seller_frozen'] = True
    return result
