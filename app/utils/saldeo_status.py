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
    r"^(?P<name>.+?)\s+Rozliczenie\s+(?P<kind>wyników\s+firmy,\s*podatków(?:\s*,\s*ZUS)?|ZUS)\s+"
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
    kind = match.group("kind").upper()
    column = "etap_kadry" if kind == "ZUS" else "etap,etap_kadry" if "ZUS" in kind else "etap"
    return match.group("name").strip(), f"{int(match.group('year')):04d}-{month:02d}", column


def _mark_complete(cur, firma_id: int, month: str) -> None:
    """Po wysłaniu wyników zaznacza „Komplet”, jeśli nie był uzupełniony."""
    import datetime
    cur.execute(
        "UPDATE statusy_miesieczne SET dokumenty_dostarczone = 1, "
        "data_dostarczenia = COALESCE(NULLIF(data_dostarczenia, ''), %s) "
        "WHERE firma_id = %s AND miesiac = %s AND COALESCE(dokumenty_dostarczone, 0) = 0",
        (datetime.date.today().isoformat(), firma_id, month),
    )


def rescan_saldeo_emails(months: Optional[set] = None) -> Dict[str, int]:
    """Jednorazowo przegląda wszystkie maile z rozliczeniami i uzupełnia etapy.

    Domyślnie obejmuje rozliczenia za bieżący i poprzedni miesiąc (maile z
    bieżącego miesiąca dotyczą zwykle poprzedniego). Nie zmienia znacznika
    ostatnio przetworzonej wiadomości.
    """
    import datetime
    if months is None:
        today = datetime.date.today()
        previous = (today.replace(day=1) - datetime.timedelta(days=1))
        months = {today.strftime("%Y-%m"), previous.strftime("%Y-%m")}
    result = {"checked": 0, "matched": 0, "unknown_company": 0, "changed": 0}
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, nazwa_saldeo_firmy AS nazwa FROM firmy "
            "WHERE COALESCE(TRIM(nazwa_saldeo_firmy), '') <> ''"
        )
        companies = {_fold(r["nazwa"]): r["id"] for r in cur.fetchall()}
        cur.execute(
            "SELECT temat FROM wiadomosci_email WHERE COALESCE(usunieta, 0) = 0 AND temat ILIKE %s ORDER BY id",
            ("%Rozliczenie%",),
        )
        for row in cur.fetchall():
            parsed = parse_subject(row["temat"] or "")
            if not parsed or parsed[1] not in months:
                continue
            result["checked"] += 1
            firma_id = companies.get(_fold(parsed[0]))
            if not firma_id:
                result["unknown_company"] += 1
                continue
            result["matched"] += 1
            for column in parsed[2].split(","):
                cur.execute(
                    f"""
                    INSERT INTO statusy_miesieczne (firma_id, miesiac, {column})
                    VALUES (%s, %s, %s)
                    ON CONFLICT (firma_id, miesiac)
                    DO UPDATE SET {column} = excluded.{column}
                    WHERE statusy_miesieczne.{column} IS DISTINCT FROM excluded.{column}
                    """,
                    (firma_id, parsed[1], ETAP),
                )
                result["changed"] += cur.rowcount or 0
            _mark_complete(cur, firma_id, parsed[1])
        conn.commit()
    finally:
        conn.close()
    return result


_startup_rescan_done = False


def rescan_on_startup() -> Optional[Dict[str, int]]:
    """Raz na uruchomienie programu uzupełnia statusy z maili o rozliczeniach."""
    global _startup_rescan_done
    if _startup_rescan_done:
        return None
    _startup_rescan_done = True
    return rescan_saldeo_emails()


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
            "SELECT id, nazwa_saldeo_firmy AS nazwa_saldeo, id AS firma_id FROM firmy "
            "WHERE COALESCE(TRIM(nazwa_saldeo_firmy), '') <> ''"
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
            for column in parsed[2].split(","):
                cur.execute(
                    f"""
                    INSERT INTO statusy_miesieczne (firma_id, miesiac, {column})
                    VALUES (%s, %s, %s)
                    ON CONFLICT (firma_id, miesiac)
                    DO UPDATE SET {column} = excluded.{column}
                    WHERE statusy_miesieczne.{column} IS DISTINCT FROM excluded.{column}
                    """,
                    (firma_id, parsed[1], ETAP),
                )
                changed += cur.rowcount or 0
            _mark_complete(cur, firma_id, parsed[1])
        conn.commit()
    finally:
        conn.close()

    if max_id > last_id:
        set_setting(LAST_ID_KEY, str(max_id))
    if changed:
        logger.info("Saldeo: ustawiono etap '%s' dla %s wpisów", ETAP, changed)
    return changed
