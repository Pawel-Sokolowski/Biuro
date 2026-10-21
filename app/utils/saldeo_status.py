"""Ustawia etap "Wysłane wyniki" w widoku miesięcznym po mailu z Saldeo.

Tytuły maili: "<Nazwa skrócona z Saldeo> Rozliczenie wyników firmy, podatków za miesiąc wrzesień 2026"
oraz "<Nazwa skrócona z Saldeo> Rozliczenie ZUS za miesiąc wrzesień 2026".
"""

from __future__ import annotations

import logging
import re
from typing import Dict, Optional, Tuple

from app.db.dao_app_settings import get_setting, set_setting
from app.db.dao_conn import get_conn

logger = logging.getLogger(__name__)

LAST_ID_KEY = "saldeo_status_last_message_id"
ETAP = "Wysłane wyniki"

MONTHS = {
    "styczeń": 1, "stycznia": 1, "luty": 2, "lutego": 2, "marzec": 3, "marca": 3,
    "kwiecień": 4, "kwietnia": 4, "maj": 5, "maja": 5, "czerwiec": 6, "czerwca": 6,
    "lipiec": 7, "lipca": 7, "sierpień": 8, "sierpnia": 8, "wrzesień": 9, "września": 9,
    "październik": 10, "października": 10, "listopad": 11, "listopada": 11,
    "grudzień": 12, "grudnia": 12,
}

SUBJECT_RE = re.compile(
    r"^(?P<name>.+?)\s+Rozliczenie\s+(?P<kind>wyników\s+firmy,\s*podatków|ZUS)\s+"
    r"za\s+miesiąc\s+(?P<month>[^\W\d_]+)\s+(?P<year>\d{4})\s*$",
    re.IGNORECASE,
)


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def parse_subject(subject: str) -> Optional[Tuple[str, str, str]]:
    """Zwraca (nazwa z Saldeo, 'YYYY-MM', kolumna etapu) albo None.

    Mail o podatkach zmienia etap księgowości, mail o ZUS - etap kadr.
    """
    cleaned = re.sub(r"^(?:(?:re|fwd?|odp|fw)\s*:\s*)+", "", (subject or "").strip(), flags=re.I)
    match = SUBJECT_RE.match(cleaned)
    if not match:
        return None
    month = MONTHS.get(match.group("month").casefold())
    if not month:
        return None
    column = "etap_kadry" if match.group("kind").upper() == "ZUS" else "etap"
    return match.group("name").strip(), f"{int(match.group('year')):04d}-{month:02d}", column


def apply_saldeo_emails() -> int:
    """Przetwarza nowe maile z Saldeo; zwraca liczbę zmienionych statusów."""
    try:
        last_id = int(get_setting(LAST_ID_KEY) or 0)
    except ValueError:
        last_id = 0

    conn = get_conn()
    changed = 0
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, nazwa_saldeo, id AS firma_id FROM firmy "
            "WHERE COALESCE(TRIM(nazwa_saldeo), '') <> ''"
        )
        companies: Dict[str, int] = {_fold(r["nazwa_saldeo"]): r["firma_id"] for r in cur.fetchall()}

        cur.execute(
            "SELECT id, temat FROM wiadomosci_email "
            "WHERE id > %s AND COALESCE(usunieta, 0) = 0 AND temat ILIKE %s ORDER BY id",
            (last_id, "%Rozliczenie%"),
        )
        rows = cur.fetchall()
        max_id = last_id
        for row in rows:
            max_id = max(max_id, row["id"])
            parsed = parse_subject(row["temat"] or "")
            if not parsed:
                continue
            firma_id = companies.get(_fold(parsed[0]))
            if not firma_id:
                continue
            cur.execute(
                f"""
                INSERT INTO statusy_miesieczne (firma_id, miesiac, {parsed[2]})
                VALUES (%s, %s, %s)
                ON CONFLICT (firma_id, miesiac)
                DO UPDATE SET {parsed[2]} = excluded.{parsed[2]}
                WHERE statusy_miesieczne.{parsed[2]} IS DISTINCT FROM excluded.{parsed[2]}
                """,
                (firma_id, parsed[1], ETAP),
            )
            changed += cur.rowcount or 0
        conn.commit()
    finally:
        conn.close()

    if max_id > last_id:
        set_setting(LAST_ID_KEY, str(max_id))
    if changed:
        logger.info("Saldeo: ustawiono etap '%s' dla %s wpisów", ETAP, changed)
    return changed
