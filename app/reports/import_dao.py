# app/reports/import_dao.py
"""Import czasu pracy i faktur z Excela oraz raport rentowności (czas × stawka vs cena usługi)."""
from typing import Any, Dict, List, Optional, Tuple
import re

from app.db.dao_conn import get_conn
from app.db.dao_monthly import contracts_by_month
from app.reports.import_parse import (
    MIN_YEAR,
    compute_employee_report,
    digits,
    fold,
    kadry_contract_type,
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


def import_invoices_file(path: str, default_month: Optional[str], overwrite: bool = False) -> Dict[str, int]:
    rows = parse_invoice_rows(read_sheet_rows(path), default_month)
    conn = get_conn()
    inserted = unmatched = 0
    kadry_totals: Dict[Tuple[int, str, str], float] = {}
    over_limit: Dict[Tuple[int, str], Tuple[float, Optional[int]]] = {}
    seen_firms = set()
    last_month = ""
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
            if firma_id is not None and r["miesiac"]:
                seen_firms.add(firma_id)
                last_month = max(last_month, r["miesiac"])
            typ = kadry_contract_type(r["pozycja"])
            if r["rodzaj"] == "ponad_limit" and firma_id is not None and r["miesiac"] and r["wartosc_netto"] > 0:
                # faktura z miesiąca M rozlicza dokumenty z miesiąca M-1
                key = (firma_id, _prev_month(r["miesiac"]))
                limit_match = re.search(r"(\d+)\s*szt", str(r["pozycja"]), re.IGNORECASE)
                prev = over_limit.get(key, (0.0, None))
                over_limit[key] = (prev[0] + float(r["wartosc_netto"]),
                                   int(limit_match.group(1)) if limit_match else prev[1])
            if firma_id is not None and typ and r["miesiac"] and r["wartosc_netto"] > 0:
                key = (firma_id, typ, r["miesiac"])
                kadry_totals[key] = kadry_totals.get(key, 0.0) + float(r["wartosc_netto"])
        updated = _apply_kadry_contracts(conn, kadry_totals)
        backfill = {
            (fid, m) for fid in seen_firms
            for m in _all_months(f"{MIN_YEAR}-01", _prev_month(last_month))
        } if last_month else set()
        docs_set = _apply_document_counts(conn, over_limit, backfill, overwrite)
        conn.commit()
    finally:
        conn.close()
    return {"wiersze": len(rows), "dodane": inserted, "pominiete": len(rows) - inserted,
            "bez_firmy": unmatched, "ceny_kadry": updated, "ilosc_dok": docs_set}


def _apply_document_counts(conn, over_limit: Dict[Tuple[int, str], Tuple[float, Optional[int]]],
                           firms_months, overwrite: bool = False) -> int:
    """Ilo?? dokument?w w miesi?cu = limit + (kwota ponad limit / cena za dokument); bez pozycji ponad limit = limit.

    firms_months: (firma_id, miesi?c) do uzupe?nienia (wszystkie miesi?ce firm z faktur).
    Bez overwrite nie nadpisuje ju? wpisanej (r??nej od 0) ilo?ci dokument?w.
    """
    sql = """
        INSERT INTO statusy_miesieczne (firma_id, miesiac, ilosc_dok) VALUES (%s, %s, %s)
        ON CONFLICT (firma_id, miesiac) DO UPDATE SET ilosc_dok = EXCLUDED.ilosc_dok
    """
    if not overwrite:
        sql += " WHERE COALESCE(statusy_miesieczne.ilosc_dok, 0) = 0"
    firms: Dict[int, Dict[str, Any]] = {}
    done = 0
    for firma_id, month in sorted(set(firms_months) | set(over_limit)):
        if firma_id not in firms:
            firms[firma_id] = conn.execute(
                """
                SELECT COALESCE(f.limit_dokumentow, 0) AS limit_dok,
                       COALESCE(c.cena_za_dokument_ponad_limit, 0) AS cena
                FROM firmy f LEFT JOIN cenniki c ON c.klucz = f.glowny_cennik
                WHERE f.id = %s
                """,
                (firma_id,),
            ).fetchone()
        firma = firms[firma_id]
        if firma is None:
            continue
        limit = int(firma["limit_dok"] or 0)
        if (firma_id, month) in over_limit:
            value, limit_in_name = over_limit[(firma_id, month)]
            if not float(firma["cena"] or 0):
                continue
            total = (limit_in_name if limit_in_name is not None else limit) + round(value / float(firma["cena"]))
        else:
            if limit <= 0:
                continue
            total = limit
        done += conn.execute(sql, (firma_id, month, total)).rowcount
    return done


def _prev_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _all_months(first: str, last: str) -> List[str]:
    y, m = int(first[:4]), int(first[5:7])
    out = []
    while f"{y}-{m:02d}" <= last:
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _apply_kadry_contracts(conn, totals: Dict[Tuple[int, str, str], float]) -> int:
    """Ilo?? um?w UoP/UZ/UoD w miesi?cu = kwota z faktury / cena z cennika firmy.

    Gdy firma nie ma cennika danego typu, ustawia cennik z kwoty faktury (jak w _apply_kadry_prices).
    """
    no_price: Dict[Tuple[int, str, str], float] = {}
    by_firm: Dict[int, Dict[str, Dict[str, int]]] = {}
    for (firma_id, typ, month), value in sorted(totals.items()):
        row = conn.execute(
            f"""
            SELECT COALESCE(c.cena_netto_kadry, c.cena, 0) AS cena
            FROM firmy f LEFT JOIN cenniki_kadry c ON c.klucz = f.{typ}_cennik
            WHERE f.id = %s
            """,
            (firma_id,),
        ).fetchone()
        price = float(row["cena"] or 0) if row else 0.0
        if price <= 0:
            no_price[(firma_id, typ, month)] = value
            continue
        by_firm.setdefault(firma_id, {}).setdefault(month, {})[f"{typ}_ilosc"] = max(0, round(value / price))
    changed = 0
    for firma_id, months in by_firm.items():
        for month in sorted(months):
            current = get_month_contracts_conn(conn, firma_id, month)
            current.update(months[month])
            conn.execute(
                """
                INSERT INTO kadry_umowy_miesieczne (firma_id, miesiac, uop_ilosc, uz_ilosc, uod_ilosc)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (firma_id, miesiac) DO UPDATE SET
                    uop_ilosc = EXCLUDED.uop_ilosc, uz_ilosc = EXCLUDED.uz_ilosc, uod_ilosc = EXCLUDED.uod_ilosc
                """,
                (firma_id, month, current["uop_ilosc"], current["uz_ilosc"], current["uod_ilosc"]),
            )
        last = conn.execute(
            "SELECT uop_ilosc, uz_ilosc, uod_ilosc FROM kadry_umowy_miesieczne "
            "WHERE firma_id = %s ORDER BY miesiac DESC LIMIT 1",
            (firma_id,),
        ).fetchone()
        conn.execute(
            "UPDATE firmy SET uop_ilosc = %s, uz_ilosc = %s, uod_ilosc = %s WHERE id = %s",
            (last["uop_ilosc"], last["uz_ilosc"], last["uod_ilosc"], firma_id),
        )
        changed += 1
    return changed + (_apply_kadry_prices(conn, no_price) if no_price else 0)


def get_month_contracts_conn(conn, firma_id: int, month: str) -> Dict[str, int]:
    row = conn.execute(
        "SELECT uop_ilosc, uz_ilosc, uod_ilosc FROM kadry_umowy_miesieczne "
        "WHERE firma_id = %s AND miesiac <= %s ORDER BY miesiac DESC LIMIT 1",
        (firma_id, month),
    ).fetchone() or conn.execute(
        "SELECT uop_ilosc, uz_ilosc, uod_ilosc FROM firmy WHERE id = %s", (firma_id,)
    ).fetchone()
    return {k: int((row[k] if row else 0) or 0) for k in ("uop_ilosc", "uz_ilosc", "uod_ilosc")}


def _apply_kadry_prices(conn, totals: Dict[Tuple[int, str, str], float]) -> int:
    """Ustawia w karcie firmy cennik UZ/UoP/UoD wg ostatniej zaimportowanej faktury (cena za umowę)."""
    latest: Dict[Tuple[int, str], Tuple[str, float]] = {}
    for (firma_id, typ, month), value in totals.items():
        if (firma_id, typ) not in latest or month > latest[(firma_id, typ)][0]:
            latest[(firma_id, typ)] = (month, value)
    changed = 0
    for (firma_id, typ), (_month, value) in latest.items():
        firma = conn.execute(
            f"SELECT {typ}_ilosc AS ilosc FROM firmy WHERE id = %s", (firma_id,)
        ).fetchone()
        if firma is None:
            continue
        price = round(value / max(int(firma["ilosc"] or 0), 1), 2)
        label = {"uop": "UoP", "uz": "UZ", "uod": "UoD"}[typ]
        found = conn.execute(
            """
            SELECT klucz FROM cenniki_kadry
            WHERE ABS(COALESCE(cena_netto_kadry, cena, 0) - %s) < 0.005
              AND klucz ILIKE %s
            ORDER BY klucz LIMIT 1
            """,
            (price, f"%{label}%"),
        ).fetchone()
        if found:
            klucz = found["klucz"]
        else:
            klucz = f"Kadry {label} {price:.2f} zł"
            conn.execute(
                """
                INSERT INTO cenniki_kadry (klucz, cena_netto_kadry, cena_brutto_kadry, opis)
                VALUES (%s, %s, %s, %s) ON CONFLICT (klucz) DO NOTHING
                """,
                (klucz, price, round(price * 1.23, 2), "Z importu faktur"),
            )
        conn.execute(f"UPDATE firmy SET {typ}_cennik = %s WHERE id = %s", (klucz, firma_id))
        changed += 1
    return changed


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
            SELECT c.firma_id, COALESCE(NULLIF(f.nazwa_skrocona, ''), f.nazwa) AS firma_nazwa, c.miesiac, c.pracownik_nazwa, c.dzial, c.sekundy
            FROM import_czas_pracy c JOIN firmy f ON f.id = c.firma_id
            WHERE c.firma_id IN ({ph_f}) AND c.miesiac IN ({ph_m})
            """,
            [*firma_ids, *months],
        ).fetchall()]
        years = sorted({m[:4] for m in months})
        ph_y = ",".join("%s" for _ in years)
        invoices = [dict(r) for r in conn.execute(
            f"""
            SELECT i.firma_id, COALESCE(NULLIF(f.nazwa_skrocona, ''), f.nazwa) AS kontrahent, i.miesiac, i.rodzaj, i.wartosc_netto
            FROM import_faktury_pozycje i JOIN firmy f ON f.id = i.firma_id
            WHERE i.firma_id IN ({ph_f}) AND substr(i.miesiac, 1, 4) IN ({ph_y})
            """,
            [*firma_ids, *years],
        ).fetchall()]
        counts: Dict[tuple, int] = {
            ("ksiegowosc", r["firma_id"], r["miesiac"]): int(r["ile"])
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
        for r in conn.execute(
            f"""
            SELECT firma_id, miesiac, ilosc_dok FROM statusy_miesieczne
            WHERE firma_id IN ({ph_f}) AND miesiac IN ({ph_m}) AND COALESCE(ilosc_dok, 0) > 0
            """,
            [*firma_ids, *months],
        ).fetchall():
            # Spójność z widokiem miesięcznym i raportem rocznym:
            # fizyczne dokumenty mają pierwszeństwo, a liczba wpisana
            # miesięcznie jest źródłem rezerwowym niezależnym od limitu.
            counts.setdefault(
                ("ksiegowosc", r["firma_id"], r["miesiac"]),
                int(r["ilosc_dok"]),
            )
        all_months = sorted({m for y in years for m in (f"{y}-{n:02d}" for n in range(1, 13))} | set(months))
        for (fid, month), total in contracts_by_month(conn, firma_ids, all_months).items():
            counts[("kadry", fid, month)] = total
        rates = {
            fold(f"{r['imie']} {r['nazwisko']}"): float(r["stawka_godzinowa"] or 0)
            for r in conn.execute(
                "SELECT imie, nazwisko, stawka_godzinowa FROM pracownicy"
            ).fetchall()
        }
    finally:
        conn.close()
    return compute_employee_report(work, invoices, rates, counts, months)
