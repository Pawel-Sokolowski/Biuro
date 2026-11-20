"""Stałe i role elementów listy poczty.

Trzymane osobno, żeby powłoka (stałe elementy UI), lista i podgląd nie importowały
nawzajem całego modułu strony poczty.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt

AUTO_SYNC_INTERVAL_MS = 120_000
ROLE_DATA = Qt.ItemDataRole.UserRole
ROLE_ACCOUNT = Qt.ItemDataRole.UserRole + 2
ROLE_ROW_ID = Qt.ItemDataRole.UserRole + 3
ROLE_FOLDER = Qt.ItemDataRole.UserRole + 4
MAX_NUMBER_KEYS = 7
ROLE_MSGID = Qt.ItemDataRole.UserRole + 20
ROLE_REFS = Qt.ItemDataRole.UserRole + 21
ROLE_FOLDER_LABEL = Qt.ItemDataRole.UserRole + 30
ROLE_FOLDER_IDS = Qt.ItemDataRole.UserRole + 31
DEFAULT_COLUMN_WIDTHS = [430, 220, 180]

__all__ = [
    'AUTO_SYNC_INTERVAL_MS', 'ROLE_DATA', 'ROLE_ACCOUNT', 'ROLE_ROW_ID', 'ROLE_FOLDER',
    'MAX_NUMBER_KEYS', 'ROLE_MSGID', 'ROLE_REFS', 'ROLE_FOLDER_LABEL', 'ROLE_FOLDER_IDS',
    'DEFAULT_COLUMN_WIDTHS',
]
