"""Credential-bearing configuration is usable by the service, never downloadable."""
from datetime import datetime
import json
import re

PRESENT = '[zapisane na serwerze]'
SECRET_KEYS = frozenset({'bio_encryption_key', 'db_url', 'haslo', 'password', 'password_hash', 'haslo_hash',
                        'private_key', 'client_secret', 'refresh_token', 'access_token', 'authorization',
                        'google_client_secret', 'google_shared_refresh_token', 'google_shared_access_token'})


def secret_name(key):
    value = str(key).casefold()
    return value in SECRET_KEYS or value.endswith('_enc') or any(part in value for part in ('password','haslo','secret','private_key','encryption_key','token'))


def public(value):
    """Preserve the shape and presence of settings, without credentials/ciphertext."""
    if isinstance(value, dict):
        setting = value.get('klucz',value.get('key'))
        if setting is not None and secret_name(setting):
            return {k: (PRESENT if v else '') if k in ('wartosc','value') else public(v) for k,v in value.items()}
        return {key: (PRESENT if item else '') if secret_name(key) else public(item) for key, item in value.items()}
    if isinstance(value, tuple) and hasattr(value, 'keys'):
        from app.db.dao_conn import Row
        clean = public(dict(value))
        return Row(value.keys(), [clean[k] for k in value.keys()])
    if isinstance(value, (tuple, list, set, frozenset)):
        return type(value)(public(v) for v in value)
    if isinstance(value, str):
        from app.diagnostics import PrivacyFilter
        from app.utils.request_cache import cached_value
        privacy = cached_value(('office_response_privacy',),PrivacyFilter)
        # Short database passwords can occur inside otherwise valid timestamps.
        if value not in privacy.secrets and re.match(r'^\d{4}-\d{2}-\d{2}(?:$|[T ])', value):
            try:
                datetime.fromisoformat(value)
            except ValueError:
                pass
            else:
                return value
        value = privacy.redact(value)
        if value.startswith('gAAAAA') or '-----BEGIN PRIVATE KEY-----' in value or '-----BEGIN ENCRYPTED PRIVATE KEY-----' in value:
            return PRESENT
        if value.lstrip().startswith(('{', '[')):
            try:
                parsed = json.loads(value)
            except (ValueError, TypeError):
                pass
            else:
                return json.dumps(public(parsed), ensure_ascii=False)
    return value


def restore(value, original):
    """A desktop saving redacted settings must not overwrite an existing secret."""
    if value == PRESENT:
        return original
    if isinstance(value, dict) and isinstance(original, dict):
        return {k: restore(v, original.get(k)) for k, v in value.items()}
    if isinstance(value, str) and isinstance(original, str) and value.lstrip().startswith(('{', '[')):
        try:
            return json.dumps(restore(json.loads(value), json.loads(original)), ensure_ascii=False)
        except (ValueError, TypeError):
            pass
    return value


def safe_error(error):
    """Configuration and database exceptions cannot export server-side secrets."""
    if isinstance(error, PermissionError):
        return 'Brak uprawnień do tej operacji.'
    if isinstance(error, ValueError):
        from app.diagnostics import PrivacyFilter
        text = PrivacyFilter().redact(str(error))
        if not re.search(r'postgres(?:ql)?://|gAAAAA|PRIVATE KEY|password\s*=', text, re.I):
            return text[:700]
    return 'Operacja na serwerze nie powiodła się. Sprawdź bieżące logi serwera.'
