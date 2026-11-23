"""Shared NIP normalization for matching existing records."""
import re

from stdnum.pl.nip import compact


def normalize_nip(value):
    # Keep the original matching contract. Legacy imports only require ten ASCII
    # digits, so checksum validation belongs at a data-entry boundary.
    text = ''.join(str(value or '').upper().split())
    if re.fullmatch(r'[PL0-9-]+', text):
        number = compact(text)
        if re.fullmatch(r'[0-9]{10}', number):
            return number
    raise ValueError('NIP musi zawierać 10 cyfr.')
