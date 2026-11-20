"""Pomocnicze funkcje listy wiadomości: wątki, etykiety pracowników, drzewo folderów."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from email.utils import getaddresses
from typing import Any, Dict, List, Optional, Tuple

from app.email.email_helpers import (SPECIAL_FOLDER_ORDER, decode_mailbox_utf7,
                                     folder_role)

_SUBJECT_PREFIX = re.compile(r"^\s*(re|odp|fw|fwd|pd|wg|aw|przekaż)\s*:\s*", re.IGNORECASE)

def group_threads(entries: List[Tuple[Any, str, str]]) -> List[List[Any]]:
    """Grupuje (element, message_id, refs) w wątki po In-Reply-To/References; kolejność jak na wejściu."""
    parent = list(range(len(entries)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_id = {mid: i for i, (_, mid, _) in enumerate(entries) if mid}
    by_ref: Dict[str, int] = {}
    for i, (_, _, refs) in enumerate(entries):
        for ref in (refs or "").split():
            if ref in by_id:
                parent[find(i)] = find(by_id[ref])
            elif ref in by_ref:
                parent[find(i)] = find(by_ref[ref])
            else:
                by_ref[ref] = i
    groups: Dict[int, List[Any]] = {}
    for i, (item, _, _) in enumerate(entries):
        groups.setdefault(find(i), []).append(item)
    return list(groups.values())

def thread_key(subject: str) -> str:
    """Temat bez prefiksów Re:/Odp:/Fw: - wiadomości o tym samym temacie tworzą wątek."""
    text = subject or ""
    while True:
        stripped = _SUBJECT_PREFIX.sub("", text)
        if stripped == text:
            break
        text = stripped
    return " ".join(text.split()).casefold()

def _fold(text: str) -> str:
    text = text.replace("ł", "l").replace("Ł", "L")
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()

def clean_folder_label(label: str, prefix: str = '') -> str:
    """Remove numbering and the locally configured office prefix."""
    label = re.sub(r"^\d+\s+", "", label.strip())
    if prefix.strip():
        label = re.sub(r"^" + re.escape(prefix.strip()) + r"\s+", "", label, flags=re.IGNORECASE)
    return label.strip()

def employee_label(label: str, employees: List[Dict[str, Any]]) -> str:
    """Zamienia np. "Kowalski Jan" na "Jan Kowalski" (niezależnie od kolejności i ogonków)."""
    key = sorted(_fold(label).split())
    for emp in employees:
        full = f"{emp.get('imie') or ''} {emp.get('nazwisko') or ''}".strip()
        if full and sorted(_fold(full).split()) == key:
            return full
    return label

def format_list_date(value: str) -> str:
    """'2026-10-15 08:00' -> '15.10.2026, 08:00' (dzisiaj tylko godzina)."""
    try:
        moment = datetime.strptime(value, "%Y-%m-%d %H:%M")
        if moment.date() == datetime.now().date():
            return moment.strftime("%H:%M")
        return moment.strftime("%d.%m.%Y, %H:%M")
    except (TypeError, ValueError):
        return value or ""

def display_names(value: str) -> str:
    """'Jan Kowalski <jan@x.pl>, b@y.pl' -> 'Jan Kowalski, b@y.pl' (sam adres tylko gdy brak nazwy)."""
    names = []
    for name, address in getaddresses([value or ""]):
        label = (name or "").strip().strip('"').strip() or address
        if label:
            names.append(label)
    return ", ".join(names) or (value or "")

def employee_tag(employee: Dict[str, Any]) -> str:
    surname = (employee.get("nazwisko") or "").strip()
    return f"{employee.get('imie') or ''} {surname[:1]}.".strip() if surname else (employee.get("imie") or "")

def employee_name(employee: Dict[str, Any]) -> str:
    return f"{employee.get('imie') or ''} {employee.get('nazwisko') or ''}".strip()

def build_folder_tree(folders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Buduje drzewo folderów wg prawdziwego separatora serwera (INBOX jako główny folder).

    Zwraca listę korzeni; węzeł: {path, label, folder (dict|None), role, children}.
    """
    nodes: Dict[str, Dict[str, Any]] = {}
    roots: List[Dict[str, Any]] = []

    def ensure(path: str, delimiter: Optional[str], folder: Optional[Dict[str, Any]]):
        node = nodes.get(path)
        if node is None:
            leaf = path.split(delimiter)[-1] if delimiter else path
            node = {
                "path": path,
                "label": decode_mailbox_utf7(leaf),
                "folder": None,
                "role": None,
                "children": [],
            }
            nodes[path] = node
            if delimiter and delimiter in path:
                parent = ensure(path.rsplit(delimiter, 1)[0], delimiter, None)
                parent["children"].append(node)
            else:
                roots.append(node)
        if folder is not None:
            node["folder"] = folder
            node["role"] = folder_role(
                folder["server_name"], folder.get("attributes"), folder.get("delimiter")
            )
        return node

    for folder in sorted(folders, key=lambda f: f["server_name"]):
        ensure(folder["server_name"], folder.get("delimiter"), folder)

    def sort_key(node: Dict[str, Any]):
        role = node["role"]
        if role in SPECIAL_FOLDER_ORDER:
            return (0, SPECIAL_FOLDER_ORDER.index(role), "")
        label = node["label"].casefold()
        # foldery numerowane ("1 Firmy", "3 Pracownicy") po kolei, jak na serwerze
        return (1, 0, [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"(\d+)", label)])

    def sort_tree(items: List[Dict[str, Any]]) -> None:
        items.sort(key=sort_key)
        for n in items:
            sort_tree(n["children"])

    sort_tree(roots)
    # INBOX zawsze pierwszy
    roots.sort(key=lambda n: 0 if n["role"] == "inbox" else 1)
    return roots
