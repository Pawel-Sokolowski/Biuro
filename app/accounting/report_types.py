"""Report identifiers independent of source spelling (RCA / ZUS RCA)."""
import re

DEFAULT_FORMS = {'accounting': ('JPK_VAT', 'VAT', 'VAT-UE', 'PIT', 'CIT', 'CIT-8'),
                 'payroll': ('ZUS RCA', 'ZUS DRA', 'ZUS RSA', 'ZUS RPA', 'PIT-11', 'PIT-4R')}


def form_key(value):
    value = re.sub(r'[^A-Z0-9]', '', str(value).upper())
    return value[3:] if value.startswith('ZUS') else value


def form_matches(value, selected):
    if form_key(selected) == 'VAT':
        return form_key(value) in ('VAT', 'VAT7', 'VAT7K')
    return form_key(value) == form_key(selected)
