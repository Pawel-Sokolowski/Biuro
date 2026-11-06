"""Shared Polish identifier checks; legacy imports warn instead of guessing."""
from stdnum.pl import nip, regon, pesel


VALIDATORS = {'nip': nip, 'regon': regon, 'pesel': pesel}


def identifier_warnings(payload):
    return [f'Niepoprawna suma kontrolna {name.upper()} — sprawdź identyfikator w arkuszu.'
            for name, validator in VALIDATORS.items()
            if payload.get(name) and not validator.is_valid(str(payload[name]))]
