"""Skanowanie maili z Saldeo i zapis rozliczeń miesięcznych do bazy."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from app.db.dao_app_settings import get_setting_first
from app.db.dao_conn import get_conn
from app.utils.saldeo_results import html_to_text, parse_saldeo_settlement

logger = logging.getLogger(__name__)

SENDER_KEY = "saldeo_nadawca_rozliczen"
DEFAULT_SENDER = "biuro@saldeo.pl"
RESULT_FIELDS = (
    "przychod", "koszty", "dochod",
    "vat_kwota", "vat_opis", "vat_okres", "vat_termin",
    "cit_kwota", "cit_okres", "cit_termin",
    "pit_kwota", "pit_okres", "pit_termin",
)


def _norm_name(value: str) -> str:
    return re.sub(r"[^\w]+", " ", str(value or "").casefold()).strip()


def find_company_id(conn, name: str) -> Optional[int]:
    wanted = _norm_name(name)
    if not wanted:
        return None
    rows = conn.execute("SELECT id, nazwa, nazwa_skrocona FROM firmy").fetchall()
    matches = {
        r["id"] for r in rows
        if wanted in (_norm_name(r["nazwa"]), _norm_name(r["nazwa_skrocona"]))
    }
    return matches.pop() if len(matches) == 1 else None


def save_settlement(conn, firma_id: int, data: Dict[str, Any], message_id: Optional[int] = None) -> None:
    cols = ", ".join(RESULT_FIELDS)
    marks = ", ".join("%s" for _ in RESULT_FIELDS)
    updates = ", ".join(f"{c} = excluded.{c}" for c in RESULT_FIELDS)
    conn.execute(
        f"""
        INSERT INTO rozliczenia_saldeo (firma_id, miesiac, {cols}, wiadomosc_id)
        VALUES (%s, %s, {marks}, %s)
        ON CONFLICT (firma_id, miesiac) DO UPDATE SET {updates},
            wiadomosc_id = excluded.wiadomosc_id,
            updated_at = to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
        """,
        (firma_id, data["miesiac"], *[data.get(c) for c in RESULT_FIELDS], message_id),
    )


def message_text(msg) -> str:
    from app.email.email_helpers import extract_html, extract_text

    html_body = extract_html(msg)
    return html_to_text(html_body) if html_body else extract_text(msg)


def scan_saldeo_messages(
    konto_id: int,
    limit: int = 50,
    fetch=None,
) -> Dict[str, Any]:
    """Przetwarza nieprzeanalizowane wiadomości od Saldeo. Zwraca {"saved": n, "warnings": [...]}."""
    if fetch is None:
        from app.email.email_sync import fetch_message_from_imap as fetch

    sender = (get_setting_first((SENDER_KEY,), DEFAULT_SENDER) or DEFAULT_SENDER).strip().lower()
    conn = get_conn()
    saved = 0
    warnings: List[str] = []
    try:
        rows = conn.execute(
            """
            SELECT w.id, w.uid, f.server_name
            FROM wiadomosci_email w JOIN foldery_email f ON f.id = w.folder_id
            WHERE w.konto_id = %s AND COALESCE(w.usunieta, 0) = 0
              AND COALESCE(w.saldeo_przetworzona, 0) = 0
              AND lower(COALESCE(w.nadawca, '')) LIKE %s
            ORDER BY w.data DESC
            LIMIT %s
            """,
            (konto_id, f"%{sender}%", limit),
        ).fetchall()
        for row in rows:
            msg = fetch(konto_id, row["server_name"], str(row["uid"]))
            if msg is None:
                continue
            parsed = parse_saldeo_settlement(message_text(msg))
            if parsed is not None:
                firma_id = find_company_id(conn, parsed["nazwa"])
                if firma_id is None:
                    warnings.append(f"Saldeo: nie znaleziono firmy „{parsed['nazwa']}” ({parsed['miesiac']}).")
                    continue
                save_settlement(conn, firma_id, parsed, row["id"])
                saved += 1
            conn.execute("UPDATE wiadomosci_email SET saldeo_przetworzona = 1 WHERE id = %s", (row["id"],))
            conn.commit()
    except Exception:
        logger.warning("Skanowanie maili Saldeo nie powiodło się", exc_info=True)
    finally:
        conn.close()
    return {"saved": saved, "warnings": warnings}
