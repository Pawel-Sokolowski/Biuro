"""Konfiguracja środowiska bez dołączania pliku .env do instalatora.

Instalator nie zawiera sekretów. Zainstalowana aplikacja czyta `.env` z folderu
użytkownika (`%LOCALAPPDATA%\\Biuro\\.env`) albo spod pliku .exe. Klucz szyfrowania
haseł e-mail jest tworzony automatycznie przy pierwszym uruchomieniu.
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
    """Zwraca klucz Fernet; gdy go brak, tworzy go i zapisuje w pliku .env użytkownika.

    Przy bazie współdzielonej (np. na NAS) skopiuj ten sam klucz na pozostałe komputery,
    bo hasła kont e-mail są zaszyfrowane tym kluczem.
    """
    existing = os.environ.get(KEY_NAME)
    if existing:
        return existing
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    path = user_env_path()
    prefix = "\n" if path.is_file() and path.read_text(encoding="utf-8").strip() else ""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{prefix}{KEY_NAME}={key}\n")
    os.environ[KEY_NAME] = key
    return key
