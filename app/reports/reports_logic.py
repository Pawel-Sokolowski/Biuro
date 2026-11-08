# app/reports/reports_logic.py
import psycopg
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Any
from app.services.calculator import oblicz_miesieczny_koszt
from app.db.dao_conn import get_conn
from app.db.dao_monthly import contracts_by_month
from app.utils.months import month_bounds
from app.security.authorization import assigned_company_ids, can, is_administrator
from app.reports.import_parse import timer_department


def _extra_timer_seconds(conn, firma_ids, months):
    """Add service time and move any counters stored under the wrong department."""
    rows = conn.execute(
        "SELECT c.firma_id, c.miesiac, c.typ, u.kategoria, SUM(c.sekundy) AS sek "
        "FROM czasomierze c JOIN uslugi_dodatkowe u ON u.id = c.usluga_id "
        "WHERE c.firma_id = ANY(%s) AND c.miesiac = ANY(%s) "
        "GROUP BY c.firma_id, c.miesiac, c.typ, u.kategoria", (firma_ids, months),
    ).fetchall()
    out = {}
    for row in rows:
        slot = out.setdefault((row['firma_id'], row['miesiac']), {'ksiegowosc': 0.0, 'kadry': 0.0})
        seconds = float(row['sek'] or 0)
        if row['typ'] in ('ksiegowosc', 'kadry'):
            slot[row['typ']] -= seconds
        slot[timer_department(row['typ'], row['kategoria'])] += seconds
    return out

# ============================================================
# LISTA FIRM
# ============================================================
def get_all_companies(pracownik_id: int | None = None) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()

    sql = "SELECT id, COALESCE(NULLIF(nazwa_skrocona, ''), nazwa) AS nazwa FROM firmy"
    params: List[int] = []
    if pracownik_id and not (
        is_administrator(pracownik_id) or can(pracownik_id, "can_view_finances")
    ):
        ids = assigned_company_ids(pracownik_id)
        if not ids:
            conn.close()
            return []
        placeholders = ",".join("%s" for _ in ids)
        sql += f" WHERE id IN ({placeholders})"
        params.extend(sorted(ids))
    cur.execute(sql + " ORDER BY 2", params)
    rows = cur.fetchall()
    conn.close()

    return [{"id": r["id"], "nazwa": r["nazwa"]} for r in rows]


def _authorized_company_ids(firma_ids: List[int], pracownik_id: int | None) -> List[int]:
    if not pracownik_id or is_administrator(pracownik_id) or can(pracownik_id, "can_view_finances"):
        return firma_ids
    allowed = assigned_company_ids(pracownik_id)
    return [firma_id for firma_id in firma_ids if firma_id in allowed]


_TYPE_LABELS = {"ksiegowosc": "Księgowość", "kadry": "Kadry", "dodatkowe": "Czynność dodatkowa", "ogolne": "Ogólne"}


def build_daily_report(employee_ids: List[int], start: str, end: str,
                       pracownik_id: int | None = None) -> List[Dict[str, Any]]:
    """Czas pracy z czasomierzy dla wybranych pracowników w dniach start..end (YYYY-MM-DD, włącznie).

    Pracownik bez uprawnień do finansów widzi wyłącznie własny czas.
    Dokumenty są średnią niezerowych miesięcy do okresu rozliczeniowego wpisu;
    umowy są miesięczną sumą UoP/UZ/UoD. Nie są to dzienne ilości wykonanej pracy."""
    see_all = not pracownik_id or is_administrator(pracownik_id) or can(pracownik_id, "can_view_finances")
    employee_ids = [int(e) for e in employee_ids if see_all or int(e) == pracownik_id]
    if not employee_ids:
        return []
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT substr(c.rozpoczecie, 1, 10) AS dzien, c.pracownik_id, c.firma_id,
                   COALESCE(NULLIF(TRIM(c.miesiac), ''), substr(c.rozpoczecie, 1, 7)) AS miesiac,
                   c.typ, c.opis, c.sekundy,
                   COALESCE(NULLIF(TRIM(COALESCE(p.imie, '') || ' ' || COALESCE(p.nazwisko, '')), ''), '(brak)') AS pracownik,
                   COALESCE(NULLIF(f.nazwa_skrocona, ''), f.nazwa, '') AS firma, u.nazwa AS usluga
            FROM czasomierze c
            LEFT JOIN pracownicy p ON p.id = c.pracownik_id
            LEFT JOIN firmy f ON f.id = c.firma_id
            LEFT JOIN uslugi_dodatkowe u ON u.id = c.usluga_id
            WHERE c.pracownik_id = ANY(%s)
              AND substr(c.rozpoczecie, 1, 10) >= %s AND substr(c.rozpoczecie, 1, 10) <= %s
            ORDER BY dzien, pracownik, firma, c.rozpoczecie
            """,
            (employee_ids, start, end),
        ).fetchall()
        company_ids = sorted({row['firma_id'] for row in rows if row['firma_id'] is not None})
        months = sorted({row['miesiac'] for row in rows if row['firma_id'] is not None})
        documents, monthly_documents, contracts, averages = {}, {}, {}, {}
        if company_ids and months:
            range_end = month_bounds(months[-1])[1]
            documents = {(row['firma_id'], row['miesiac']): int(row['ilosc'] or 0) for row in conn.execute(
                "SELECT firma_id, substr(data_wyst, 1, 7) AS miesiac, COUNT(*) AS ilosc FROM dokumenty "
                "WHERE firma_id = ANY(%s) AND data_wyst < %s "
                "AND data_wyst ~ '^[0-9]{4}-(0[1-9]|1[0-2])-' "
                "GROUP BY firma_id, substr(data_wyst, 1, 7)", (company_ids, range_end),
            ).fetchall()}
            monthly_documents = {(row['firma_id'], row['miesiac']): int(row['ilosc_dok'] or 0) for row in conn.execute(
                "SELECT firma_id, miesiac, ilosc_dok FROM statusy_miesieczne "
                "WHERE firma_id = ANY(%s) AND miesiac <= %s", (company_ids, months[-1]),
            ).fetchall()}
            contracts = contracts_by_month(conn, company_ids, months)
            history = {**monthly_documents, **documents}
            # One prefix scan per company; each requested month is a binary
            # lookup instead of rescanning all companies' entire history.
            from bisect import bisect_right
            by_company = {}
            for (company_id, period), count in history.items():
                if count > 0:
                    by_company.setdefault(company_id, []).append((period, count))
            prefixes = {}
            for company_id, entries in by_company.items():
                entries.sort()
                periods, sums = [], [0]
                for period, count in entries:
                    periods.append(period)
                    sums.append(sums[-1] + count)
                prefixes[company_id] = periods, sums
            for company_id, month in {(row['firma_id'], row['miesiac']) for row in rows if row['firma_id'] is not None}:
                periods, sums = prefixes.get(company_id, ([], [0]))
                count = bisect_right(periods, month)
                averages[company_id, month] = ((Decimal(sums[count]) / Decimal(count)).quantize(
                    Decimal('0.01'), rounding=ROUND_HALF_UP) if count else None, count)
    finally:
        conn.close()
    totals: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        activity = row["opis"] or row["usluga"] or _TYPE_LABELS.get(row["typ"], row["typ"])
        pair = row['firma_id'], row['miesiac']
        key = row['dzien'], row['pracownik_id'], *pair, activity
        firm = row['firma_id'] is not None
        entry = totals.setdefault(key, {
            'data': row['dzien'], 'pracownik': row['pracownik'], 'firma': row['firma'],
            'firma_id': row['firma_id'], 'miesiac': row['miesiac'] if firm else '',
            'czynnosc': activity, 'sekundy': 0,
            'dokumenty': documents.get(pair, monthly_documents.get(pair, 0)) if firm else None,
            'dokumenty_srednia': averages.get(pair, (None, 0))[0],
            'dokumenty_srednia_miesiace': averages.get(pair, (None, 0))[1],
            'umowy': contracts.get(pair, 0) if firm else None,
        })
        entry['sekundy'] += int(row['sekundy'] or 0)
    return sorted(totals.values(), key=lambda row: (
        row['data'], row['pracownik'], row['firma'], row['miesiac'], row['czynnosc']))


# ============================================================
# RANGE MIESIĘCY
# ============================================================
def get_months_range(start: str, end: str) -> List[str]:
    from datetime import datetime
    out = []

    s = datetime.strptime(start, "%Y-%m")
    e = datetime.strptime(end, "%Y-%m")

    cur = s
    while cur <= e:
        out.append(cur.strftime("%Y-%m"))
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)

    return out


# ============================================================
# GODZINY DLA FIRMY I MIESIĄCA
# ============================================================
def get_imported_seconds(firma_ids: List[int], months: List[str]) -> Dict[tuple, Dict[str, float]]:
    """Zaimportowany czas pracy: (firma_id, miesiąc) -> {'ksiegowosc': s, 'kadry': s}."""
    if not firma_ids or not months:
        return {}
    ph_f = ",".join("%s" for _ in firma_ids)
    ph_m = ",".join("%s" for _ in months)
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT firma_id, miesiac, dzial, SUM(sekundy) AS sek
            FROM import_czas_pracy
            WHERE firma_id IN ({ph_f}) AND miesiac IN ({ph_m})
            GROUP BY firma_id, miesiac, dzial
            """,
            [*firma_ids, *months],
        ).fetchall()
    finally:
        conn.close()
    out: Dict[tuple, Dict[str, float]] = {}
    for r in rows:
        slot = out.setdefault((r["firma_id"], r["miesiac"]), {"ksiegowosc": 0.0, "kadry": 0.0})
        key = "kadry" if r["dzial"] in ("kadry", "inne_kadry") else "ksiegowosc"
        slot[key] += float(r["sek"] or 0)
    return out


def get_hours_for_company_month(firma_id: int, miesiac: str) -> Dict[str, float]:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT sekundy_ksiegowosc, sekundy_kadry
        FROM statusy_miesieczne
        WHERE firma_id = %s AND miesiac = %s
    """, (firma_id, miesiac))

    row = cur.fetchone()
    extra_time = _extra_timer_seconds(conn, [firma_id], [miesiac]).get((firma_id, miesiac), {})
    conn.close()

    imported = get_imported_seconds([firma_id], [miesiac]).get((firma_id, miesiac), {})
    return {
        "ksiegowosc": float((row["sekundy_ksiegowosc"] if row else 0) or 0) + imported.get("ksiegowosc", 0.0) + extra_time.get('ksiegowosc', 0),
        "kadry": float((row["sekundy_kadry"] if row else 0) or 0) + imported.get("kadry", 0.0) + extra_time.get('kadry', 0),
    }


# ============================================================
# USŁUGI DODATKOWE — SUMA
# ============================================================
def get_extra_services_value(firma_id: int, miesiac: str) -> float:
    return sum(get_extra_services_by_department(firma_id, miesiac).values())


def get_extra_services_by_department(firma_id: int, miesiac: str) -> Dict[str, float]:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT u.kategoria, SUM(uf.ilosc * uf.cena_netto) AS suma
        FROM uslugi_dodatkowe_firma uf JOIN uslugi_dodatkowe u ON u.id = uf.usluga_id
        WHERE uf.firma_id = %s AND uf.miesiac = %s AND uf.fakturowac = 1
        GROUP BY u.kategoria
    """, (firma_id, miesiac))

    rows = cur.fetchall()
    conn.close()
    totals = {'ksiegowosc': 0.0, 'kadry': 0.0}
    for row in rows:
        totals[timer_department('', row['kategoria'])] += float(row['suma'] or 0)
    return totals


def build_summary_report(firma_ids, months, pracownik_id=None):
    """Compatibility view of the canonical invoice/time profitability dataset."""
    from app.reports.import_dao import build_profitability_report
    return [dict(row, ksiegowa=row['pracownik'], typ=row['sekcja'], cena_za_usluge=row['cena'])
            for row in build_profitability_report(firma_ids, months, pracownik_id)]


# ============================================================
# RAPORT MIESIĘCZNY — POPRAWIONY
# ============================================================
def build_report(
    firma_ids: List[int],
    months: List[str],
    pracownik_id: int | None = None,
) -> Dict[str, Any]:
    firma_ids = _authorized_company_ids(firma_ids, pracownik_id)
    if not firma_ids or not months:
        return {}

    placeholders_firm = ",".join("%s" for _ in firma_ids)
    placeholders_month = ",".join("%s" for _ in months)
    range_start = month_bounds(min(months))[0]
    range_end = month_bounds(max(months))[1]
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            WITH selected_months(miesiac) AS (
                SELECT %s {" UNION ALL SELECT %s" * (len(months) - 1)}
            ),
            doc_counts AS (
                SELECT firma_id, substr(data_wyst, 1, 7) AS miesiac, COUNT(*) AS dokumenty
                FROM dokumenty
                WHERE firma_id IN ({placeholders_firm})
                  AND data_wyst >= %s AND data_wyst < %s
                GROUP BY firma_id, substr(data_wyst, 1, 7)
            ),
            extras AS (
                SELECT uf.firma_id, uf.miesiac, SUM(uf.ilosc * uf.cena_netto) AS extra,
                       SUM(CASE WHEN u.kategoria ILIKE '%%kadr%%' OR u.kategoria ILIKE '%%płac%%'
                                     OR u.kategoria ILIKE '%%plac%%'
                                THEN uf.ilosc * uf.cena_netto ELSE 0 END) AS extra_kadry
                FROM uslugi_dodatkowe_firma uf JOIN uslugi_dodatkowe u ON u.id = uf.usluga_id
                WHERE uf.firma_id IN ({placeholders_firm})
                  AND uf.miesiac IN ({placeholders_month})
                  AND uf.fakturowac = 1
                GROUP BY uf.firma_id, uf.miesiac
            )
            SELECT f.id AS firma_id, m.miesiac,
                   COALESCE(sm.sekundy_ksiegowosc, 0) AS sekundy_ksiegowosc,
                   COALESCE(sm.sekundy_kadry, 0) AS sekundy_kadry,
                   -- Te same dane co w widoku miesięcznym: rzeczywiste dokumenty
                   -- z tabeli dokumenty albo ręczna/importowana liczba miesięczna.
                   -- Limit firmy wpływa wyłącznie na cenę, nigdy na tę liczbę.
                   COALESCE(NULLIF(dc.dokumenty, 0), sm.ilosc_dok, 0) AS dokumenty,
                   COALESCE(ex.extra, 0) AS extra,
                   COALESCE(ex.extra_kadry, 0) AS extra_kadry,
                   COALESCE(c.netto, c.cena_netto_ksiegowosc, 0) AS netto,
                   COALESCE(c.brutto, c.cena_brutto_ksiegowosc, 0) AS brutto,
                   CASE WHEN COALESCE(f.wliczac_zdrowotna, 0) = 1 THEN 0
                        ELSE COALESCE(NULLIF(f.zdrowotna, 0), c.zdrowotna, 0) END AS zdrowotna,
                   COALESCE(f.limit_dokumentow, 0) AS limit_dokumentow,
                   COALESCE(f.uop_ilosc, 0) + COALESCE(f.uz_ilosc, 0) + COALESCE(f.uod_ilosc, 0) AS umowy,
                   COALESCE(NULLIF(f.cena_ponad_limit, 0), c.cena_za_dokument_ponad_limit, 0) AS cena_za_dokument
            FROM firmy f
            CROSS JOIN selected_months m
            LEFT JOIN statusy_miesieczne sm
              ON sm.firma_id = f.id AND sm.miesiac = m.miesiac
            LEFT JOIN doc_counts dc ON dc.firma_id = f.id AND dc.miesiac = m.miesiac
            LEFT JOIN extras ex ON ex.firma_id = f.id AND ex.miesiac = m.miesiac
            LEFT JOIN cenniki c ON c.klucz = f.glowny_cennik
            WHERE f.id IN ({placeholders_firm})
            """,
            [
                *months,
                *firma_ids, range_start, range_end,
                *firma_ids, *months,
                *firma_ids,
            ],
        ).fetchall()
    finally:
        conn.close()

    imported = get_imported_seconds(firma_ids, months)
    conn = get_conn()
    try:
        contracts = contracts_by_month(conn, firma_ids, months)
        extra_seconds = _extra_timer_seconds(conn, firma_ids, months)
    finally:
        conn.close()
    out = {firma_id: {} for firma_id in firma_ids}
    for row in rows:
        docs = int(row["dokumenty"] or 0)
        limit = int(row["limit_dokumentow"] or 0)
        extra_docs = max(0, docs - limit) if limit else 0
        netto = float(row["netto"] or 0) + extra_docs * float(row["cena_za_dokument"] or 0)
        extra = float(row["extra"] or 0)
        extra_kadry = float(row['extra_kadry'] or 0)
        koszt_ksieg = netto + extra - extra_kadry
        koszt_kadry = float(row["zdrowotna"] or 0) + extra_kadry
        imp = imported.get((row["firma_id"], row["miesiac"]), {})
        extra_time = extra_seconds.get((row['firma_id'], row['miesiac']), {})
        godz_ksieg = float(row["sekundy_ksiegowosc"] or 0) + imp.get("ksiegowosc", 0.0) + extra_time.get('ksiegowosc', 0)
        godz_kadry = float(row["sekundy_kadry"] or 0) + imp.get("kadry", 0.0) + extra_time.get('kadry', 0)
        out[row["firma_id"]][row["miesiac"]] = {
            "koszt_ksiegowosc": round(koszt_ksieg, 2),
            "koszt_kadry": round(koszt_kadry, 2),
            "godziny_ksiegowosc": godz_ksieg,
            "godziny_kadry": godz_kadry,
            "koszt_na_godzine_ksiegowosc": koszt_ksieg / (godz_ksieg / 3600) if godz_ksieg else 0,
            "koszt_na_godzine_kadry": koszt_kadry / (godz_kadry / 3600) if godz_kadry else 0,
            "extra": extra,
            "dokumenty": docs,
            "umowy": contracts.get((row["firma_id"], row["miesiac"]), int(row["umowy"] or 0)),
        }
    return out


# ============================================================
# RAPORT ROCZNY — wszystkie 12 miesięcy
# ============================================================
def build_yearly_report(firma_id: int, year: int) -> Dict[str, Any]:
    months = [f"{year}-{m:02d}" for m in range(1, 12 + 1)]
    return build_report([firma_id], months)[firma_id]


# ============================================================
# PORÓWNANIE FIRM — POPRAWIONE
# ============================================================
def compare_companies(firma_ids, miesiac, pracownik_id=None):
    """Compare actual billed department revenue and tracked time."""
    from app.reports.import_dao import build_profitability_report
    allowed = _authorized_company_ids(firma_ids, pracownik_id)
    out = {fid: dict(koszt_ksiegowosc=0.0, koszt_kadry=0.0, godziny_ksiegowosc=0,
                    godziny_kadry=0, extra=0.0) for fid in allowed}
    for row in build_profitability_report(allowed, [miesiac], pracownik_id):
        slot = out[row['firma_id']]
        slot['koszt_'+row['sekcja']] += row['cena']
        slot['godziny_'+row['sekcja']] += row['sekundy']
        slot['extra'] += row['cena_dodatkowa']
    for slot in out.values():
        for department in ('ksiegowosc','kadry'):
            seconds = slot['godziny_'+department]
            slot['koszt_na_godzine_'+department] = slot['koszt_'+department] / (seconds / 3600) if seconds else 0
    return out


def build_profit_report(firma_ids, months, pracownik_id=None):
    """Compatibility hourly revenue report, using the same actual data as profitability."""
    from app.reports.import_dao import build_profitability_report
    allowed = _authorized_company_ids(firma_ids, pracownik_id)
    totals = {(fid, month): {'ksiegowosc': [0.0,0], 'kadry': [0.0,0], 'extra': 0.0}
              for fid in allowed for month in months}
    for row in build_profitability_report(allowed, months, pracownik_id):
        slot = totals[row['firma_id'], row['miesiac']]
        slot[row['sekcja']][0] += row['cena']
        slot[row['sekcja']][1] += row['sekundy']
        slot['extra'] += row['cena_dodatkowa']
    out = {fid: {} for fid in allowed}
    for (fid, month), slot in totals.items():
        result = {'extra': slot['extra']}
        for department in ('ksiegowosc','kadry'):
            price, seconds = slot[department]
            result['profit_'+department] = price / (seconds / 3600) if seconds else 0
        out[fid][month] = result
    return out
