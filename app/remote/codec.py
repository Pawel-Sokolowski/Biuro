"""Bounded JSON values, never pickle, executable types or arbitrary object loading."""
import base64
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

MAX_DEPTH = 40


def encode(value, depth=0):
    if depth > MAX_DEPTH:
        raise ValueError('Dane odpowiedzi są zbyt zagnieżdżone.')
    child = lambda v: encode(v, depth + 1)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return {'$type': 'decimal', 'value': str(value)}
    if isinstance(value, (datetime, date, time)):
        return {'$type': type(value).__name__, 'value': value.isoformat()}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {'$type': 'bytes', 'value': base64.b64encode(value).decode('ascii')}
    if isinstance(value, Path):
        return str(value)
    from email.message import Message
    if isinstance(value, Message):
        return {'$type': 'email', 'value': base64.b64encode(value.as_bytes()).decode('ascii')}
    # Database Row is tuple-compatible and also provides named lookup.
    if isinstance(value, tuple) and hasattr(value, 'keys'):
        return {'$type': 'row', 'names': list(value.keys()), 'values': [child(v) for v in value]}
    if isinstance(value, dict):
        return {'$type': 'mapping', 'items': [[child(k), child(v)] for k, v in value.items()]}
    if isinstance(value, (tuple, set, frozenset)):
        return {'$type': 'tuple' if isinstance(value, tuple) else 'set', 'items': [child(v) for v in value]}
    if isinstance(value, list):
        return [child(v) for v in value]
    from dataclasses import fields, is_dataclass
    if is_dataclass(value) and not isinstance(value, type):
        name = type(value).__module__ + '.' + type(value).__name__
        from app.remote.value_types import TYPES
        if name in TYPES:
            values = {f.name: child(getattr(value,f.name)) for f in fields(value)}
            if name=='app.db.dao_active_timers.ActiveTimer':
                values['base_seconds'] = value.elapsed()
                values.pop('monotonic_start',None)
            return {'$type': 'record', 'name': name, 'fields': values}
    raise ValueError('Nieobsługiwany typ danych operacji: ' + type(value).__name__)


def decode(value, depth=0):
    if depth > MAX_DEPTH:
        raise ValueError('Dane żądania są zbyt zagnieżdżone.')
    child = lambda v: decode(v, depth + 1)
    if isinstance(value, list):
        return [child(v) for v in value]
    if not isinstance(value, dict):
        if value is None or isinstance(value, (str, bool, int, float)):
            return value
        raise ValueError('Nieprawidłowy typ danych.')
    kind = value.get('$type')
    if kind == 'decimal':
        return Decimal(value['value'])
    if kind in ('date', 'datetime', 'time'):
        return {'date': date, 'datetime': datetime, 'time': time}[kind].fromisoformat(value['value'])
    if kind == 'bytes':
        return base64.b64decode(value['value'], validate=True)
    if kind == 'email':
        from email import policy
        from email.parser import BytesParser
        return BytesParser(policy=policy.default).parsebytes(base64.b64decode(value['value'],validate=True))
    if kind == 'mapping':
        return {child(k): child(v) for k, v in value['items']}
    if kind in ('tuple', 'set'):
        return (tuple if kind == 'tuple' else set)(child(v) for v in value['items'])
    if kind == 'row':
        from app.db.dao_conn import Row
        return Row(value['names'], [child(v) for v in value['values']])
    if kind == 'record':
        from importlib import import_module
        from app.remote.value_types import TYPES
        if value['name'] not in TYPES:
            raise ValueError('Nieznany typ rekordu.')
        module, name = value['name'].rsplit('.', 1)
        return getattr(import_module(module), name)(**{k: child(v) for k, v in value['fields'].items()})
    raise ValueError('Nieznany format danych operacji.')
