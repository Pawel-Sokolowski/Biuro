# app/reports/import_dao.py
"""Import czasu pracy i faktur z Excela oraz raport rentowności (czas × stawka vs cena usługi)."""
from typing import Any, Dict, List, Optional, Tuple

from app.db.dao_conn import get_conn
from app.reports.import_parse import (
    MIN_YEAR,
    compute_employee_report,
    digits,
    fold,
    parse_invoice_rows,
    parse_work_rows,
)
from app.reports.reports_logic import _authorized_company_ids


def read_sheet_rows(path: str) -> List[tuple]:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        return [tuple(r) for r in wb.active.iter_rows(values_only=True)]
    finally:
        wb.close()


def _company_index(conn) -> Tuple[Dict[str, int], Dict[str, int]]:
    by_nip: Dict[str, int] = {}
    by_name: Dict[str, int] = {}
    for r in conn.execute(
        "SELECT id, nip, nazwa, nazwa_skrocona, nazwa_saldeo FROM firmy"
    ).fetchall():
        nip = digits(r["nip"])
        if nip:
            by_nip.setdefault(nip, r["id"])
        for field in ("nazwa", "nazwa_skrocona", "nazwa_saldeo"):
            key = fold(r[field])
            if key:
                by_name.setdefault(key, r["id"])
    return by_nip, by_name


def _match_company(index, nip: str, *names: str) -> Optional[int]:
    by_nip, by_name = index
    if nip and nip in by_nip:
        return by_nip[nip]
    for name in names:
        key = fold(name)
        if key and key in by_name:
            return by_name[key]
    return None


def import_work_file(path: str) -> Dict[str, int]:
    rows = parse_work_rows(read_sheet_rows(path))
    conn = get_conn()
    inserted = unmatched = 0
    try:
        index = _company_index(conn)
        employees = {
            fold(f"{r['imie']} {r['nazwisko']}"): r["id"]
            for r in conn.execute("SELECT id, imie, nazwisko FROM pracownicy").fetchall()
        }
        for r in rows:
            firma_id = _match_company(index, r["nip"], r["firma_nazwa"])
            if firma_id is None:
                unmatched += 1
            cur = conn.execute(
                """
                INSERT INTO import_czas_pracy
                    (hash, firma_id, firma_nazwa, nip, pracownik_id, pracownik_nazwa, miesiac_dok,
                     data, miesiac, od, do_, sekundy, pauza_sekundy, czynnosc, komentarz, dzial)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (hash) DO UPDATE SET
                    firma_id = EXCLUDED.firma_id, miesiac = EXCLUDED.miesiac,
                    dzial = EXCLUDED.dzial, pracownik_id = EXCLUDED.pracownik_id
                """,
                (
                    r["hash"], firma_id, r["firma_nazwa"], r["nip"],
                    employees.get(fold(r["pracownik_nazwa"])), r["pracownik_nazwa"], r["miesiac_dok"],
                    r["data"], r["miesiac"], r["od"], r["do"], r["sekundy"], r["pauza_sekundy"],
                    r["czynnosc"], r["komentarz"], r["dzial"],
                ),
            )
            inserted += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return {"wiersze": len(rows), "dodane": inserted, "pominiete": len(rows) - inserted,
            "bez_firmy": unmatched}


def import_invoices_file(path: str, default_month: Optional[str]) -> Dict[str, int]:
    rows = parse_invoice_rows(read_sheet_rows(path), default_month)
    conn = get_conn()
    inserted = unmatched = 0
    try:
        index = _company_index(conn)
        for r in rows:
            firma_id = _match_company(index, r["nip"], r["kontrahent"])
            if firma_id is None:
                unmatched += 1
            cur = conn.execute(
                """
                INSERT INTO import_faktury_pozycje
                    (hash, firma_id, kontrahent, nip, nr_faktury, pozycja, indeks,
                     wartosc_netto, waluta, miesiac, rodzaj)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (hash) DO UPDATE SET
                    firma_id = EXCLUDED.firma_id, miesiac = EXCLUDED.miesiac,
                    rodzaj = EXCLUDED.rodzaj
                """,
                (
                    r["hash"], firma_id, r["kontrahent"], r["nip"], r["nr_faktury"], r["pozycja"],
                    r["indeks"], r["wartosc_netto"], r["waluta"], r["miesiac"], r["rodzaj"],
                ),
            )
            inserted += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return {"wiersze": len(rows), "dodane": inserted, "pominiete": len(rows) - inserted,
            "bez_firmy": unmatched}


def list_employee_rates() -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, imie, nazwisko, stawka_godzinowa FROM pracownicy "
            "WHERE aktywny = 1 ORDER BY nazwisko, imie"
        ).fetchall()
    finally:
        conn.close()
    return [
        {"id": r["id"], "nazwa": f"{r['imie']} {r['nazwisko']}", "stawka": r["stawka_godzinowa"]}
        for r in rows
    ]


def set_employee_rates(rates: Dict[int, Optional[float]]) -> None:
    conn = get_conn()
    try:
        for emp_id, rate in rates.items():
            conn.execute("UPDATE pracownicy SET stawka_godzinowa = %s WHERE id = %s", (rate, emp_id))
        conn.commit()
    finally:
        conn.close()


def delete_imported(
    what: str, months: List[str], firma_ids: Optional[List[int]] = None, include_unmatched: bool = False
) -> int:
    """Usuwa zaimportowane dane. what: 'czas', 'faktury' lub 'oba'. Zwraca liczbę usuniętych wierszy."""
    tables = {"czas": ["import_czas_pracy"], "faktury": ["import_faktury_pozycje"],
              "oba": ["import_czas_pracy", "import_faktury_pozycje"]}[what]
    if not months:
        return 0
    ph_m = ",".join("%s" for _ in months)
    deleted = 0
    conn = get_conn()
    try:
        for table in tables:
            sql = f"DELETE FROM {table} WHERE miesiac IN ({ph_m})"
            params: List[Any] = list(months)
            if firma_ids:
                ph_f = ",".join("%s" for _ in firma_ids)
                cond = f"firma_id IN ({ph_f})"
                if include_unmatched:
                    cond = f"({cond} OR firma_id IS NULL)"
                sql += f" AND {cond}"
                params += list(firma_ids)
            deleted += conn.execute(sql, params).rowcount
        conn.commit()
    finally:
        conn.close()
    return deleted


def build_profitability_report(
    firma_ids: List[int], months: List[str], pracownik_id: Optional[int] = None
) -> List[Dict[str, Any]]:
    firma_ids = _authorized_company_ids(firma_ids, pracownik_id)
    months = [m for m in months if int(m[:4]) >= MIN_YEAR]
    if not firma_ids or not months:
        return []
    ph_f = ",".join("%s" for _ in firma_ids)
    ph_m = ",".join("%s" for _ in months)
    conn = get_conn()
    try:
        work = [dict(r) for r in conn.execute(
            f"""
            SELECT c.firma_id, f.nazwa AS firma_nazwa, c.miesiac, c.pracownik_nazwa, c.dzial, c.sekundy
            FROM import_czas_pracy c JOIN firmy f ON f.id = c.firma_id
            WHERE c.firma_id IN ({ph_f}) AND c.miesiac IN ({ph_m})
            """,
            [*firma_ids, *months],
        ).fetchall()]
        invoices = [dict(r) for r in conn.execute(
            f"""
            SELECT i.firma_id, f.nazwa AS kontrahent, i.miesiac, i.rodzaj, i.wartosc_netto
            FROM import_faktury_pozycje i JOIN firmy f ON f.id = i.firma_id
            WHERE i.firma_id IN ({ph_f}) AND i.miesiac IN ({ph_m})
            """,
            [*firma_ids, *months],
        ).fetchall()]
        docs = {
            (r["firma_id"], r["miesiac"]): int(r["ile"])
            for r in conn.execute(
                f"""
                SELECT firma_id, substr(data_wyst, 1, 7) AS miesiac, COUNT(*) AS ile
                FROM dokumenty
                WHERE firma_id IN ({ph_f}) AND substr(data_wyst, 1, 7) IN ({ph_m})
                GROUP BY firma_id, substr(data_wyst, 1, 7)
                """,
                [*firma_ids, *months],
            ).fetchall()
        }
        rates = {
            fold(f"{r['imie']} {r['nazwisko']}"): float(r["stawka_godzinowa"] or 0)
            for r in conn.execute(
                "SELECT imie, nazwisko, stawka_godzinowa FROM pracownicy"
            ).fetchall()
        }
    finally:
        conn.close()
    return compute_employee_report(work, invoices, rates, docs)
