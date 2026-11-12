"""Konfiguracja środowiska bez dołączania pliku .env do instalatora.

Instalator nie zawiera sekretów. Zainstalowana aplikacja czyta `.env` z folderu
użytkownika (`%LOCALAPPDATA%\\Biuro\\.env`) albo spod pliku .exe. Klucz szyfrowania
haseł jest wspólny dla całego biura i musi zostać jawnie skonfigurowany.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List

from dotenv import load_dotenv

KEY_NAME = "BIO_ENCRYPTION_KEY"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def user_env_path() -> Path:
    if is_frozen():
        base = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "Biuro"
        base.mkdir(parents=True, exist_ok=True)
        return base / ".env"
    return PROJECT_ROOT / ".env"


def env_search_paths() -> List[Path]:
    paths = [user_env_path()]
    if is_frozen():
        paths.append(Path(sys.executable).resolve().parent / ".env")
    return paths


def load_environment() -> List[Path]:
    """Wczytuje istniejące pliki .env (pierwszy ma pierwszeństwo). Zwraca użyte ścieżki."""
    loaded = []
    for path in env_search_paths():
        if path.is_file():
            load_dotenv(dotenv_path=path, override=False)
            loaded.append(path)
    return loaded


def ensure_encryption_key() -> str:
    """Require an explicitly provisioned deployment key; never invent a PC key."""
    from cryptography.fernet import Fernet
    existing = os.environ.get(KEY_NAME)
    if not existing:
        raise ValueError('Brak wspólnego klucza szyfrowania. Otwórz Ustawienia → Klucz szyfrowania i zaimportuj klucz z działającego komputera. Nowe biuro wymaga jednego wspólnego klucza BIO_ENCRYPTION_KEY.')
    try:
        Fernet(existing.encode())
    except (ValueError, TypeError):
        raise ValueError('Nieprawidłowy BIO_ENCRYPTION_KEY. Zaimportuj wspólny klucz biura.') from None
    return existing


def install_encryption_key(key: str) -> None:
    """Persist a locally supplied shared key without changing other settings."""
    from cryptography.fernet import Fernet
    import tempfile
    key = key.strip()
    Fernet(key.encode())
    path = user_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding='utf-8') if path.exists() else ''
    import re
    lines = [line for line in text.splitlines() if not re.match(r'^\s*(?:export\s+)?' + KEY_NAME + r'\s*=', line)]
    lines.append(KEY_NAME + '=' + key)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write('\n'.join(lines) + '\n')
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    os.environ[KEY_NAME] = key
