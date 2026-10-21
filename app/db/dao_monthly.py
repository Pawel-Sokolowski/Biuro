# app/db/dao_monthly.py
import psycopg
from typing import Dict, Any, Optional
from app.db.dao_conn import get_conn
from app.utils.months import month_bounds


def get_month_status(firma_id: int, miesiac: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM statusy_miesieczne
        WHERE firma_id = %s AND miesiac = %s
    """, (firma_id, miesiac))

    row = cur.fetchone()
    conn.close()

    return dict(row) if row else None


def save_month_status(firma_id: int, miesiac: str, payload: Dict[str, Any]) -> None:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO statusy_miesieczne (
            firma_id, miesiac,
            etap, ilosc_dok,
            sekundy_ksiegowosc, sekundy_kadry,
            wartosc_uslug, dokumenty_dostarczone, data_dostarczenia, etap_kadry
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, 'Brak'))
        ON CONFLICT(firma_id, miesiac)
        DO UPDATE SET
            etap = excluded.etap,
            ilosc_dok = excluded.ilosc_dok,
            sekundy_ksiegowosc = excluded.sekundy_ksiegowosc,
            sekundy_kadry = excluded.sekundy_kadry,
            wartosc_uslug = excluded.wartosc_uslug,
            dokumenty_dostarczone = excluded.dokumenty_dostarczone,
            data_dostarczenia = excluded.data_dostarczenia,
            etap_kadry = COALESCE(%s, statusy_miesieczne.etap_kadry)
    """, (
        firma_id,
        miesiac,
        payload.get("etap", "Brak"),
        payload.get("ilosc_dok", 0),
        payload.get("sekundy_ksiegowosc", 0),
        payload.get("sekundy_kadry", 0),
        payload.get("wartosc_uslug", 0.0),
        1 if payload.get("dokumenty_dostarczone") else 0,
        payload.get("data_dostarczenia") if payload.get("dokumenty_dostarczone") else None,
        payload.get("etap_kadry"),
        payload.get("etap_kadry"),
    ))

    conn.commit()
    conn.close()


def count_documents_for_month(firma_id: int, miesiac: str) -> int:
    conn = get_conn()
    cur = conn.cursor()

    start, end = month_bounds(miesiac)
    cur.execute("""
        SELECT COUNT(*) AS cnt
        FROM dokumenty
        WHERE firma_id = %s
          AND data_wyst >= %s
          AND data_wyst < %s
    """, (firma_id, start, end))

    row = cur.fetchone()
    conn.close()

    return int(row["cnt"] or 0)


def get_month_contracts(firma_id: int, miesiac: str) -> Dict[str, int]:
    """Ilość umów w danym miesiącu: wpis miesięczny, a gdy go brak - ostatni wcześniejszy, a potem dane firmy."""
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT uop_ilosc, uz_ilosc, uod_ilosc FROM kadry_umowy_miesieczne
            WHERE firma_id = %s AND miesiac <= %s
            ORDER BY miesiac DESC LIMIT 1
            """,
            (firma_id, miesiac),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT uop_ilosc, uz_ilosc, uod_ilosc FROM firmy WHERE id = %s", (firma_id,)
            ).fetchone()
    finally:
        conn.close()
    row = row or {}
    return {k: int((row.get(k) if row else 0) or 0) for k in ("uop_ilosc", "uz_ilosc", "uod_ilosc")}


def save_month_contracts(firma_id: int, miesiac: str, uop: int, uz: int, uod: int) -> None:
    """Zapisuje ilość umów w miesiącu; jeśli to najnowszy miesiąc, aktualizuje też kartę firmy."""
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO kadry_umowy_miesieczne (firma_id, miesiac, uop_ilosc, uz_ilosc, uod_ilosc)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (firma_id, miesiac) DO UPDATE SET
                uop_ilosc = excluded.uop_ilosc,
                uz_ilosc = excluded.uz_ilosc,
                uod_ilosc = excluded.uod_ilosc
            """,
            (firma_id, miesiac, uop, uz, uod),
        )
        newer = conn.execute(
            "SELECT 1 FROM kadry_umowy_miesieczne WHERE firma_id = %s AND miesiac > %s LIMIT 1",
            (firma_id, miesiac),
        ).fetchone()
        if newer is None:
            conn.execute(
                "UPDATE firmy SET uop_ilosc = %s, uz_ilosc = %s, uod_ilosc = %s WHERE id = %s",
                (uop, uz, uod, firma_id),
            )
        conn.commit()
    finally:
        conn.close()


def contracts_by_month(conn, firma_ids, months) -> Dict[tuple, int]:
    """{(firma_id, miesiac): suma umów} z przenoszeniem ostatniej znanej wartości na kolejne miesiące."""
    if not firma_ids:
        return {}
    ph = ",".join("%s" for _ in firma_ids)
    defaults = {
        r["id"]: int(r["umowy"] or 0)
        for r in conn.execute(
            f"SELECT id, COALESCE(uop_ilosc,0)+COALESCE(uz_ilosc,0)+COALESCE(uod_ilosc,0) AS umowy "
            f"FROM firmy WHERE id IN ({ph})",
            list(firma_ids),
        ).fetchall()
    }
    entries: Dict[int, list] = {}
    for r in conn.execute(
        f"SELECT firma_id, miesiac, uop_ilosc+uz_ilosc+uod_ilosc AS umowy "
        f"FROM kadry_umowy_miesieczne WHERE firma_id IN ({ph}) ORDER BY miesiac",
        list(firma_ids),
    ).fetchall():
        entries.setdefault(r["firma_id"], []).append((r["miesiac"], int(r["umowy"] or 0)))
    out: Dict[tuple, int] = {}
    for fid in firma_ids:
        for month in months:
            value = defaults.get(fid, 0)
            for entry_month, total in entries.get(fid, []):
                if entry_month <= month:
                    value = total
            out[(fid, month)] = value
    return out

