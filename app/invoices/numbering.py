"""Validated invoice series templates; number allocation remains transactional."""
import re
from string import Formatter

KEY = 'faktury_wzor_numeracji'
DEFAULT = '{prefix}/{number:04d}/{month:02d}/{year}'


def compile_pattern(template, prefix, issued):
    if not template or len(template) > 160 or any(ord(c) < 32 for c in template):
        raise ValueError('Wzór numeracji musi mieć od 1 do 160 znaków.')
    parts, seen = [], []
    try:
        for literal, name, spec, conversion in Formatter().parse(template):
            parts.append(re.escape(literal))
            if name is None:
                continue
            if name not in {'prefix', 'number', 'month', 'year'} or conversion:
                raise ValueError('Dozwolone pola: {prefix}, {number:04d}, {month:02d}, {year}.')
            if spec and (name == 'prefix' or not re.fullmatch(r'0?[1-9]d', spec)):
                raise ValueError('Nieprawidłowy format liczby we wzorze numeracji.')
            seen.append(name)
            value = {'prefix': prefix, 'month': issued.month, 'year': issued.year}.get(name)
            parts.append(r'(?P<number>\d+)' if name == 'number' else re.escape(format(value, spec)))
    except (ValueError, KeyError) as exc:
        raise ValueError(f'Nieprawidłowy wzór numeracji: {exc}') from exc
    if seen.count('number') != 1 or seen.count('year') != 1 or len(seen) != len(set(seen)):
        raise ValueError('Wzór musi zawierać dokładnie jedno pole number i year; pola nie mogą się powtarzać.')
    if re.search(r'\{number[^}]*\}\{(?:month|year)', template) or re.search(r'\{(?:month|year)[^}]*\}\{number', template):
        raise ValueError('Oddziel numer kolejny od daty separatorem, np. /.')
    return re.compile(''.join(parts) + r'\Z')


def render(template, prefix, issued, number=1):
    compile_pattern(template, prefix, issued)
    return template.format(prefix=prefix, number=number, month=issued.month, year=issued.year)


def series_template(template, kind):
    """Keep each document kind separate even in legacy templates with a literal FV."""
    if kind in (None, 'faktura') or any(name == 'prefix' for _, name, _, _ in Formatter().parse(template)):
        return template
    if template.upper().startswith('FV/'):
        return '{prefix}/' + template[3:]
    return '{prefix}/' + template
