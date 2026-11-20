"""Report identifiers independent of source spelling (RCA / ZUS RCA)."""
import re

# Kolejność i brzmienie etykiet widocznych dla użytkownika (Raporty).
DEFAULT_FORMS = {'accounting': ('JPK_VAT', 'VAT-7', 'VAT-7K', 'VAT-UE', 'VAT-27', 'VAT-9M',
                                'VAT-8', 'PIT', 'CIT-8', 'CIT-8E'),
                 'payroll': ('ZUS RCA', 'ZUS DRA', 'ZUS RSA', 'ZUS RPA', 'ZUS RZA',
                             'ZUS ZUA', 'ZUS ZZA', 'ZUS ZWUA', 'ZUS ZIUA', 'ZUS ZCNA', 'PIT-11', 'PIT-4R')}

# Nazwy formularzy w interfejsie — zawsze skrót, nie opis słowny.
FORM_LABELS = {'JPKVAT': 'JPK_VAT', 'JPK_VAT': 'JPK_VAT',
               'VAT': 'VAT-7', 'VAT7': 'VAT-7', 'VAT-7': 'VAT-7',
               'VAT7K': 'VAT-7K', 'VAT-7K': 'VAT-7K',
               'VATUE': 'VAT-UE', 'VAT-UE': 'VAT-UE',
               'VAT27': 'VAT-27', 'VAT-27': 'VAT-27',
               'VAT9M': 'VAT-9M', 'VAT-9M': 'VAT-9M',
               'VAT8': 'VAT-8', 'VAT-8': 'VAT-8',
               'PIT': 'PIT', 'CIT': 'CIT', 'CIT8': 'CIT-8', 'CIT-8': 'CIT-8',
               'CIT8E': 'CIT-8E', 'CIT-8E': 'CIT-8E',
               'DRA': 'DRA', 'RCA': 'RCA', 'RSA': 'RSA', 'RPA': 'RPA', 'RZA': 'RZA',
               'ZUA': 'ZUA', 'ZZA': 'ZZA', 'ZWUA': 'ZWUA', 'ZIUA': 'ZIUA', 'ZCNA': 'ZCNA',
               'IWA': 'IWA', 'ZSWA': 'ZSWA', 'ZFA': 'ZFA', 'VAT12': 'VAT-12'}

# PIT-y roczne i zaliczki, których dziś nie wysyłamy — pokazujemy nazwę, ale nie
# traktujemy braku wysyłki jako zaległości (żółte/ostrzegawcze oznaczenia).
NOT_SENT_FORMS = {'PIT', 'PIT-5', 'PIT-5L'}


def form_label(value):
    """Skrót formularza do wszelkich napisów; nazwa pełna zostaje tylko w danych."""
    return FORM_LABELS.get(form_key(value), str(value))


def declaration_title(value):
    """Tytuł pozycji w Raportach: „Deklaracja VAT-7”, „Deklaracja PIT”."""
    key = form_key(value)
    if key in ('JPKVAT', 'JPK_VAT', 'VAT', 'VAT7', 'VAT-7', 'VAT7K', 'VAT-7K', 'VATUE', 'VAT-UE',
               'VAT27', 'VAT-27', 'VAT9M', 'VAT-9M', 'VAT8', 'VAT-8', 'PIT', 'CIT', 'CIT8',
               'CIT-8', 'CIT8E', 'CIT-8E'):
        return 'Deklaracja ' + form_label(value)
    return form_label(value)


def pit_form(company):
    """Który PIT dotyczy firmy: PIT-5L (liniowy), PIT-5 (skala/pozostałe)."""
    taxation = str(company.get('forma_opodatkowania') or '').strip().casefold()
    return 'PIT-5L' if 'liniow' in taxation else 'PIT-5'
EVENT_FORMS = {'ZUA', 'ZZA', 'ZWUA', 'ZIUA', 'ZCNA'}


def form_key(value):
    value = re.sub(r'[^A-Z0-9]', '', str(value).upper())
    return value[3:] if value.startswith('ZUS') else value


VAT_KEYS = ('VAT', 'VAT7', 'VAT7K')


def form_matches(value, selected):
    wanted, key = form_key(selected), form_key(value)
    if wanted in VAT_KEYS:
        # Raport VAT to deklaracja: VAT-7 i VAT-7K to ten sam obowiązek.
        return key in VAT_KEYS
    return key == wanted
