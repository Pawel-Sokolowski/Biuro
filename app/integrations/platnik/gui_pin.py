"""PIN for the operator's Płatnik certificate, stored under the Windows user."""
from app.remote import credential_store


PIN_TARGET = 'Biuro/Platnik/CertificatePIN'


def read_pin():
    return credential_store.read_named(PIN_TARGET)


def save_pin(value):
    if not 1 <= len(value) <= 64 or not value.isprintable() or value != value.strip():
        raise ValueError('Podaj PIN certyfikatu bez spacji na początku i końcu.')
    if not credential_store.save_named(PIN_TARGET, value, 'Płatnik certificate PIN'):
        raise OSError('Menedżer poświadczeń Windows jest niedostępny.')


def forget_pin():
    credential_store.delete_named(PIN_TARGET)
    if read_pin() is not None:
        raise OSError('Nie usunięto PIN-u z Menedżera poświadczeń Windows.')
