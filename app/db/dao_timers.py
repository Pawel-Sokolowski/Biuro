from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.db.dao_conn import get_conn

SOURCE_TIMER = "stoper"
SOURCE_IMPORT = "import"
_STATUS_COLUMNS = {"ksiegowosc": "sekundy_ksiegowosc", "kadry": "sekundy_kadry"}


def list_company_timers(firma_id: int) -> List[Dict[str, Any]]:
    """Rekordy czasu firmy: ze stopera oraz zaimportowane (najnowsze pierwsze)."""
    conn = get_conn()
    try:
        rows = [
            {"zrodlo": SOURCE_TIMER, "id": r["id"], "miesiac": r["miesiac"], "start": r["rozpoczecie"],
             "koniec": r["zakonczenie"], "pracownik": r["pracownik"] or "", "dzial": r["typ"],
             "opis": r["opis"] or "", "sekundy": int(r["sekundy"] or 0)}
            for r in conn.execute(
                "SELECT c.id, c.miesiac, c.rozpoczecie, c.zakonczenie, c.typ, c.opis, c.sekundy, "
                "p.imie || ' ' || p.nazwisko AS pracownik "
                "FROM czasomierze c LEFT JOIN pracownicy p ON p.id = c.pracownik_id "
                "WHERE c.firma_id = %s",
                (firma_id,),
            ).fetchall()
        ]
        rows += [
            {"zrodlo": SOURCE_IMPORT, "id": r["id"], "miesiac": r["miesiac"],
             "start": f"{r['data']} {r['od'] or ''}".strip(), "koniec": f"{r['data']} {r['do_'] or ''}".strip(),
             "pracownik": r["pracownik_nazwa"] or "", "dzial": r["dzial"],
             "opis": " ".join(filter(None, [r["czynnosc"], r["komentarz"]])), "sekundy": int(r["sekundy"] or 0)}
            for r in conn.execute(
                "SELECT id, miesiac, data, od, do_, pracownik_nazwa, dzial, czynnosc, komentarz, sekundy "
                "FROM import_czas_pracy WHERE firma_id = %s",
                (firma_id,),
            ).fetchall()
        ]
    finally:
        conn.close()
    rows.sort(key=lambda r: (r["start"], r["id"]), reverse=True)
    return rows


def delete_company_timers(firma_id: int, items: Iterable[Tuple[str, int]], actor_id: Optional[int]) -> int:
    """Usuwa wskazane rekordy czasu firmy; dla stopera koryguje też sumę czasu w widoku miesięcznym."""
    if not actor_id:
        raise PermissionError("Brak zalogowanego pracownika.")
    deleted = 0
    conn = get_conn()
    try:
        for source, record_id in items:
            if source == SOURCE_IMPORT:
                deleted += conn.execute(
                    "DELETE FROM import_czas_pracy WHERE id = %s AND firma_id = %s", (record_id, firma_id)
                ).rowcount
            elif source == SOURCE_TIMER:
                row = conn.execute(
                    "DELETE FROM czasomierze WHERE id = %s AND firma_id = %s RETURNING miesiac, typ, sekundy",
                    (record_id, firma_id),
                ).fetchone()
                if not row:
                    continue
                deleted += 1
                column = _STATUS_COLUMNS.get(row["typ"])
                if column:
                    conn.execute(
                        f"UPDATE statusy_miesieczne SET {column} = GREATEST(0, COALESCE({column}, 0) - %s) "
                        "WHERE firma_id = %s AND miesiac = %s",
                        (int(row["sekundy"] or 0), firma_id, row["miesiac"]),
                    )
            else:
                raise ValueError(f"Nieznane źródło rekordu: {source}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return deleted
