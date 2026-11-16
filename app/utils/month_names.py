"""Polish calendar month names, without changing imported month numbers."""
import re

MONTHS_PL = ('styczeń', 'luty', 'marzec', 'kwiecień', 'maj', 'czerwiec',
             'lipiec', 'sierpień', 'wrzesień', 'październik', 'listopad', 'grudzień')


def display_months(value):
    if value is None:
        return ''
    text = str(value)
    return re.sub(r'(?<![\w.-])(?:0?[1-9]|1[0-2])(?![\w.-])',
                  lambda match: MONTHS_PL[int(match.group())-1], text)
