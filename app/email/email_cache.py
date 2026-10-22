"""Plikowa pamięć podręczna pełnych wiadomości (.eml) obok bazy danych."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional
import threading

_cache_prune_lock = threading.Lock()
from app.config import EMAIL_LOCAL_LIMIT, get_data_dir

logger = logging.getLogger(__name__)


def cache_dir(konto_id: int) -> Path:
    path = Path(get_data_dir()).resolve() / "email_cache" / str(int(konto_id))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(konto_id: int, folder: str, uid: str) -> Path:
    key = hashlib.sha1(f"{folder}\x00{uid}".encode("utf-8")).hexdigest()
    return cache_dir(konto_id) / f"{key}.eml"


def has_message(konto_id: int, folder: str, uid: str) -> bool:
    return _path(konto_id, folder, str(uid)).is_file()


def read_message(konto_id: int, folder: str, uid: str) -> Optional[bytes]:
    path = _path(konto_id, folder, str(uid))
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        logger.warning("Nie można odczytać cache wiadomości", exc_info=True)
        return None


def write_message(
    konto_id: int, folder: str, uid: str, raw: bytes, limit: int = EMAIL_LOCAL_LIMIT
) -> None:
    try:
        _path(konto_id, folder, str(uid)).write_bytes(raw)
        prune(konto_id, limit)
    except OSError:
        logger.warning("Nie można zapisać cache wiadomości", exc_info=True)


def remove_message(konto_id: int, folder: str, uid: str) -> None:
    try:
        _path(konto_id, folder, str(uid)).unlink(missing_ok=True)
    except OSError:
        logger.warning("Nie można usunąć cache wiadomości", exc_info=True)


def prune(konto_id: int, limit: int = EMAIL_LOCAL_LIMIT) -> int:
    if limit <= 0:
        return 0

    with _cache_prune_lock:
        files = sorted(
            cache_dir(konto_id).glob("*.eml"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        removed = 0

        for old in files[limit:]:
            try:
                old.unlink(missing_ok=True)
                removed += 1
            except OSError:
                logger.warning(
                    "Nie można usunąć %s",
                    old,
                    exc_info=True,
                )

        return removed