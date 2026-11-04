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
             "opis": r["opis"] or "", "sekundy": int(r["sekundy"] or 0),
             "usluga_id": r["usluga_id"], "usluga": r["usluga"] or ""}
            for r in conn.execute(
                "SELECT c.id, c.miesiac, c.rozpoczecie, c.zakonczenie, c.typ, c.opis, c.sekundy, c.usluga_id, "
                "u.nazwa AS usluga, p.imie || ' ' || p.nazwisko AS pracownik "
                "FROM czasomierze c LEFT JOIN pracownicy p ON p.id = c.pracownik_id "
                "LEFT JOIN uslugi_dodatkowe u ON u.id = c.usluga_id "
                "WHERE c.firma_id = %s",
                (firma_id,),
            ).fetchall()
        ]
        rows += [
            {"zrodlo": SOURCE_IMPORT, "id": r["id"], "miesiac": r["miesiac"],
             "start": f"{r['data']} {r['od'] or ''}".strip(), "koniec": f"{r['data']} {r['do_'] or ''}".strip(),
             "pracownik": r["pracownik_nazwa"] or "", "dzial": r["dzial"],
             "opis": " ".join(filter(None, [r["czynnosc"], r["komentarz"]])), "sekundy": int(r["sekundy"] or 0),
             "usluga_id": None, "usluga": ""}
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


DEPARTMENTS = [
    ("ksiegowosc", "Księgowość"),
    ("kadry", "Kadry"),
    ("dodatkowe", "Czynności dodatkowe"),
    ("ogolne", "Inne"),
]


def list_extra_service_catalogue() -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        return [{"id": r["id"], "nazwa": r["nazwa"]}
                for r in conn.execute("SELECT id, nazwa FROM uslugi_dodatkowe ORDER BY nazwa").fetchall()]
    finally:
        conn.close()


def add_extra_service_to_catalogue(nazwa: str, actor_id: Optional[int], cena_netto: float = 0.0,
                                   stawka_vat: str = "23") -> int:
    """Dodaje czynność dodatkową do katalogu (lub zwraca istniejącą o tej nazwie)."""
    if not actor_id:
        raise PermissionError("Brak zalogowanego pracownika.")
    nazwa = (nazwa or "").strip()
    if not nazwa:
        raise ValueError("Nazwa czynności jest wymagana.")
    conn = get_conn()
    try:
        row = conn.execute("SELECT id FROM uslugi_dodatkowe WHERE LOWER(nazwa) = LOWER(%s)", (nazwa,)).fetchone()
        if row:
            return row["id"]
        row = conn.execute(
            "INSERT INTO uslugi_dodatkowe (nazwa, cena_netto, stawka_vat, kategoria) "
            "VALUES (%s, %s, %s, 'księgowość') RETURNING id", (nazwa, cena_netto, stawka_vat)).fetchone()
        conn.commit()
        return row["id"]
    finally:
        conn.close()


def update_company_timer(firma_id: int, source: str, record_id: int, dzial: str, opis: str,
                         sekundy: int, actor_id: Optional[int], usluga_id: Optional[int] = None) -> bool:
    """Zmienia dział, opis i czas rekordu; dla stopera koryguje sumy w widoku miesięcznym."""
    if not actor_id:
        raise PermissionError("Brak zalogowanego pracownika.")
    if dzial not in {key for key, _ in DEPARTMENTS}:
        raise ValueError(f"Nieznany dział: {dzial}")
    sekundy = max(0, int(sekundy))
    opis = (opis or "").strip()
    conn = get_conn()
    try:
        if source == SOURCE_IMPORT:
            changed = conn.execute(
                "UPDATE import_czas_pracy SET dzial = %s, czynnosc = %s, sekundy = %s "
                "WHERE id = %s AND firma_id = %s", (dzial, opis, sekundy, record_id, firma_id),
            ).rowcount
        elif source == SOURCE_TIMER:
            old = conn.execute(
                "SELECT miesiac, typ, sekundy FROM czasomierze WHERE id = %s AND firma_id = %s",
                (record_id, firma_id),
            ).fetchone()
            if not old:
                conn.rollback()
                return False
            conn.execute(
                "UPDATE czasomierze SET typ = %s, opis = %s, sekundy = %s, usluga_id = %s "
                "WHERE id = %s AND firma_id = %s",
                (dzial, opis, sekundy, usluga_id if dzial == "dodatkowe" else None, record_id, firma_id),
            )
            for typ, delta in ((old["typ"], -int(old["sekundy"] or 0)), (dzial, sekundy)):
                column = _STATUS_COLUMNS.get(typ)
                if column and delta:
                    conn.execute(
                        f"UPDATE statusy_miesieczne SET {column} = GREATEST(0, COALESCE({column}, 0) + %s) "
                        "WHERE firma_id = %s AND miesiac = %s", (delta, firma_id, old["miesiac"]),
                    )
            changed = 1
        else:
            raise ValueError(f"Nieznane źródło rekordu: {source}")
        conn.commit()
        return bool(changed)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
