"""Report identifiers independent of source spelling (RCA / ZUS RCA)."""
import re

DEFAULT_FORMS = {'accounting': ('JPK_VAT', 'VAT', 'VAT-UE', 'VAT-8', 'VAT-9M', 'VAT-12', 'VAT-27', 'PIT', 'CIT', 'CIT-8'),
                 'payroll': ('ZUS RCA', 'ZUS DRA', 'ZUS RSA', 'ZUS RPA', 'ZUS RZA',
                             'ZUS ZUA', 'ZUS ZZA', 'ZUS ZWUA', 'ZUS ZIUA', 'ZUS ZCNA', 'PIT-11', 'PIT-4R')}
EVENT_FORMS = {'ZUA', 'ZZA', 'ZWUA', 'ZIUA', 'ZCNA'}


def form_key(value):
    value = re.sub(r'[^A-Z0-9]', '', str(value).upper())
    return value[3:] if value.startswith('ZUS') else value


def form_matches(value, selected):
    if form_key(selected) == 'VAT':
        return form_key(value) in ('VAT', 'VAT7', 'VAT7K')
    return form_key(value) == form_key(selected)
