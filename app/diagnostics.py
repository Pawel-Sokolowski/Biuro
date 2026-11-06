"""Installed-app logs with bounded disk use and credential redaction."""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
from urllib.parse import urlparse, unquote

from app.version import __version__
from app.db.reliability_schema import SCHEMA_VERSION


class PrivacyFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        self.secrets = []
        key = os.environ.get('BIO_ENCRYPTION_KEY')
        if key:
            self.secrets.append(key)
        try:
            password = urlparse(os.environ.get('DB_URL', '')).password
            if password:
                self.secrets.extend([password, unquote(password)])
        except ValueError:
            pass

    def redact(self, message):
        for secret in self.secrets:
            if secret:
                message = message.replace(secret, '[redacted]')
        return message

    def filter(self, record):
        record.msg, record.args = self.redact(record.getMessage()), ()
        return True


class RedactingFormatter(logging.Formatter):
    def __init__(self):
        super().__init__('%(asctime)s %(levelname)s %(name)s %(message)s')
        self.privacy = PrivacyFilter()

    def format(self, record):
        return self.privacy.redact(super().format(record))


def configure_logging(directory=None):
    from app.config import get_data_dir
    directory = Path(directory) if directory else get_data_dir() / 'logs'
    directory.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if any(getattr(handler, '_biuro_handler', False) for handler in root.handlers):
        return directory / 'biuro.log'
    handler = RotatingFileHandler(directory / 'biuro.log', maxBytes=5 * 1024 * 1024,
                                 backupCount=4, encoding='utf-8')
    handler._biuro_handler = True
    handler.setFormatter(RedactingFormatter())
    handler.addFilter(PrivacyFilter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    root.info('application_start version=%s schema_expected=%s python=%s frozen=%s', __version__, SCHEMA_VERSION, sys.version.split()[0], bool(getattr(sys, 'frozen', False)))
    return directory / 'biuro.log'
