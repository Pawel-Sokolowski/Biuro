import json
from datetime import datetime
from typing import Dict, List, Optional

from app.db.dao_conn import get_conn

# Spółki płacące CIT (gdy forma opodatkowania nie wskazuje deklaracji)
CIT_LEGAL_FORMS = ("z o.o.", "akcyjna", "komandytowa", "fundacja", "stowarzyszenie")


def inferred_main_declaration(company: dict) -> str:
    form = (company.get("forma_opodatkowania") or "").strip().lower()
    legal = (company.get("forma_dzialalnosci") or "").strip().lower()
    if "estoń" in form or "estonsk" in form:
        return "CIT-8E"
    if "ryczałt" in form:
        return "PIT-28"
    if "liniow" in form:
        return "PIT-36L"
    if "skala" in form or "ogólne" in form or "progresywn" in form:
        return "PIT-36"
    if "księgi" in form or "cit" in form:
        return "CIT-8"
    if any(key in legal for key in CIT_LEGAL_FORMS):
        return "CIT-8"
    if "ryczałt" in legal:
        return "PIT-28"
    return ""


def company_declarations(company: dict) -> List[str]:
    """Deklaracje roczne firmy: podstawowa (pole firmy lub z formy opodatkowania) + dodatkowe."""
    if company.get("roczne_deklaracje") is not None:
        return list(dict.fromkeys(json.loads(company["roczne_deklaracje"])))
    codes = []
    main = (company.get("rozliczenie_roczne") or "").strip() or inferred_main_declaration(company)
    if main:
        codes.append(main)
    for part in (company.get("deklaracje_dodatkowe") or "").replace(";", ",").split(","):
        code = part.strip().upper()
        if code and code not in codes:
            codes.append(code)
    return codes


def _seed_company_snapshot(conn, year: int, company: dict) -> None:
    company = dict(company)
    if conn.execute(
        "SELECT 1 FROM year_end_snapshots WHERE rok = %s AND firma_id = %s",
        (year, company["id"]),
    ).fetchone():
        return
    codes = company_declarations(company)
    for saved in conn.execute(
        "SELECT deklaracja FROM zakonczenie_roku "
        "WHERE firma_id = %s AND rok = %s AND owner_key = 0",
        (company["id"], year),
    ).fetchall():
        if saved[0] not in codes:
            codes.append(saved[0])
    company["declaration_codes"] = codes
    conn.execute(
        "INSERT INTO year_end_snapshots(rok, firma_id, company_json) "
        "VALUES (%s, %s, %s) ON CONFLICT (rok, firma_id) DO NOTHING",
        (year, company["id"], json.dumps(company, ensure_ascii=False, default=str)),
    )


def _ensure_year_snapshots(conn, year: int, firma_id: Optional[int] = None) -> None:
    # Check missing snapshots in a batch. Existing snapshots are immutable here.
    params = [year, year, year]
    sql = """SELECT f.* FROM firmy f
             WHERE (COALESCE(f.status, 'Aktywne') = 'Aktywne'
                    OR f.id IN (SELECT firma_id FROM zakonczenie_roku WHERE rok=%s)
                    OR f.id IN (SELECT firma_id FROM owner_year_declarations WHERE rok=%s))
               AND NOT EXISTS (SELECT 1 FROM year_end_snapshots s WHERE s.rok=%s AND s.firma_id=f.id)"""
    if firma_id is not None:
        sql += " AND f.id=%s"
        params.append(firma_id)
    missing = conn.execute(sql, params).fetchall()
    if not missing:
        return
    conn.execute("SELECT pg_advisory_xact_lock(748204)")
    # A concurrent editor may have created snapshots while we waited.
    missing = conn.execute(sql, params).fetchall()
    filed = {}
    for row in conn.execute(
        "SELECT firma_id, deklaracja FROM zakonczenie_roku WHERE rok=%s AND owner_key=0", (year,)
    ):
        filed.setdefault(row[0], []).append(row[1])
    payloads = []
    for row in missing:
        company = dict(row)
        codes = company_declarations(company)
        codes += [code for code in filed.get(company["id"], []) if code not in codes]
        company["declaration_codes"] = codes
        payloads.append((year, company["id"], json.dumps(company, ensure_ascii=False, default=str)))
    if payloads:
        conn.cursor().executemany(
            "INSERT INTO year_end_snapshots(rok, firma_id, company_json) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            payloads,
        )
    conn.execute("INSERT INTO year_end_snapshot_years(rok) VALUES (%s) ON CONFLICT DO NOTHING", (year,))


def ensure_company_year_snapshot(firma_id: int, year: int) -> None:
    with get_conn() as conn:
        _ensure_year_snapshots(conn, year, firma_id)


def list_year_end(year: int) -> List[Dict]:
    """Use durable annual snapshots without per-company queries on every read."""
    with get_conn() as conn:
        _ensure_year_snapshots(conn, year)
        companies = [json.loads(r[0]) for r in conn.execute(
            "SELECT company_json FROM year_end_snapshots WHERE rok=%s ORDER BY firma_id", (year,)
        )]
        owners = {}
        for r in conn.execute("SELECT * FROM owner_year_declarations WHERE rok=%s", (year,)):
            owners.setdefault(r["firma_id"], []).append(dict(r))
        saved, saved_codes = {}, {}
        for r in conn.execute("SELECT * FROM zakonczenie_roku WHERE rok=%s", (year,)):
            saved[(r["firma_id"], r["deklaracja"], r["owner_key"])] = dict(r)
            saved_codes.setdefault((r["firma_id"], r["owner_key"]), []).append(r["deklaracja"])
    result = []
    for company in companies:
        declarations = [
            {"kod": code, **saved.get((company["id"], code, 0), {})}
            for code in company.get("declaration_codes", company_declarations(company))
        ]
        for owner in owners.get(company["id"], []):
            codes = json.loads(owner["declarations_json"])
            codes += [code for code in saved_codes.get((company["id"], owner["owner_id"]), []) if code not in codes]
            for code in codes:
                declarations.append({"kod": code, "owner_key": owner["owner_id"], "owner_name": owner["owner_name"], **saved.get((company["id"], code, owner["owner_id"]), {})})
        company["deklaracje"] = declarations
        result.append(company)
    return result


def set_company_declarations(firma_id: int, main: str, extra: str, *, year: int, actor_id: int) -> None:
    ensure_company_year_snapshot(firma_id, year)
    with get_conn() as conn:
        row = conn.execute("SELECT company_json FROM year_end_snapshots WHERE rok = %s AND firma_id = %s FOR UPDATE", (year, firma_id)).fetchone()
        if not row:
            raise ValueError("Firma nie znajduje się w wybranym roku.")
        company = json.loads(row[0])
        company.update(rozliczenie_roczne=main or "", deklaracje_dodatkowe=extra or "", roczne_deklaracje=None)
        company["declaration_codes"] = company_declarations(company)
        # Never hide a filing that already has recorded history.
        for saved in conn.execute("SELECT deklaracja FROM zakonczenie_roku WHERE rok = %s AND firma_id = %s AND owner_key = 0", (year, firma_id)).fetchall():
            if saved[0] not in company["declaration_codes"]:
                company["declaration_codes"].append(saved[0])
        conn.execute("UPDATE year_end_snapshots SET company_json = %s WHERE rok = %s AND firma_id = %s", (json.dumps(company, ensure_ascii=False), year, firma_id))
        conn.execute("INSERT INTO audit_log(tabela, rekord_id, akcja, uzytkownik_id, szczegoly) VALUES ('year_end_snapshots', %s, 'edycja', %s, %s)", (firma_id, actor_id, json.dumps({"year": year, "main": main, "extra": extra})))


def save_declaration(actor_id: int, firma_id: int, year: int, code: str, *,
                     filed: bool, filed_date: Optional[str] = None,
                     amount: Optional[float] = None, upo: str = "", notes: str = "", owner_key: int = 0) -> None:
    ensure_company_year_snapshot(firma_id, year)
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO zakonczenie_roku
                (firma_id, rok, deklaracja, zlozona, data_zlozenia, kwota, upo, uwagi, updated_by, updated_at, owner_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (firma_id, rok, deklaracja, owner_key) DO UPDATE SET
                zlozona = EXCLUDED.zlozona, data_zlozenia = EXCLUDED.data_zlozenia,
                kwota = EXCLUDED.kwota, upo = EXCLUDED.upo, uwagi = EXCLUDED.uwagi,
                updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at
            """,
            (firma_id, year, code, 1 if filed else 0, filed_date or None, amount,
             upo or "", notes or "", actor_id, datetime.now().isoformat(timespec="seconds"), owner_key),
        )
        conn.commit()
    finally:
        conn.close()


def get_company_year_declarations(firma_id: int, year: int) -> list[str]:
    """An unconfigured year starts from the company's defaults without writing a snapshot."""
    with get_conn() as conn:
        row = conn.execute("SELECT company_json FROM year_end_snapshots WHERE rok = %s AND firma_id = %s", (year, firma_id)).fetchone()
        if row:
            company = json.loads(row[0])
            return company.get("declaration_codes", company_declarations(company))
        row = conn.execute("SELECT * FROM firmy WHERE id = %s", (firma_id,)).fetchone()
        return company_declarations(dict(row)) if row else []


def save_company_year_declarations(firma_id: int, selections: dict[int, list[str]], actor_id: int) -> None:
    from app.security.authorization import can
    if actor_id is not None and not can(actor_id, "can_edit_companies"):
        raise PermissionError("Brak uprawnień do edycji firmy.")
    with get_conn() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(748204)")
        company = conn.execute("SELECT * FROM firmy WHERE id = %s", (firma_id,)).fetchone()
        if not company:
            raise ValueError("Nie znaleziono firmy.")
        for year, codes in selections.items():
            if not 2000 <= int(year) <= 2100:
                raise ValueError("Nieprawidłowy rok.")
            row = conn.execute("SELECT company_json FROM year_end_snapshots WHERE rok = %s AND firma_id = %s FOR UPDATE", (year, firma_id)).fetchone()
            if not row:
                _seed_company_snapshot(conn, year, dict(company))
                row = conn.execute("SELECT company_json FROM year_end_snapshots WHERE rok = %s AND firma_id = %s FOR UPDATE", (year, firma_id)).fetchone()
            snapshot = json.loads(row[0])
            codes = list(dict.fromkeys(codes))
            # Recorded filings remain visible even after an obligation is unticked.
            for saved in conn.execute("SELECT deklaracja FROM zakonczenie_roku WHERE rok = %s AND firma_id = %s AND owner_key = 0", (year, firma_id)).fetchall():
                if saved[0] not in codes:
                    codes.append(saved[0])
            snapshot["declaration_codes"] = codes
            snapshot["roczne_deklaracje"] = json.dumps(codes, ensure_ascii=False)
            conn.execute("INSERT INTO year_end_snapshots(rok, firma_id, company_json) VALUES (%s, %s, %s) ON CONFLICT (rok, firma_id) DO UPDATE SET company_json = EXCLUDED.company_json", (year, firma_id, json.dumps(snapshot, ensure_ascii=False, default=str)))
            conn.execute("INSERT INTO audit_log(tabela, rekord_id, akcja, uzytkownik_id, szczegoly) VALUES ('year_end_snapshots', %s, 'edycja', %s, %s)", (firma_id, actor_id, json.dumps({"year": year, "declarations": codes})))


def get_owner_year_declarations(firma_id, year, owner_id):
    with get_conn() as conn:
        row = conn.execute("SELECT declarations_json FROM owner_year_declarations WHERE firma_id=%s AND rok=%s AND owner_id=%s", (firma_id, year, owner_id)).fetchone()
        return json.loads(row[0]) if row else []


def save_owner_year_declarations(firma_id, selections, actor_id):
    from app.security.authorization import can
    if actor_id is not None and not can(actor_id, "can_edit_companies"):
        raise PermissionError("Brak uprawnień do edycji firmy.")
    with get_conn() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(748204)")
        for (year, owner_id), codes in selections.items():
            if not 2000 <= int(year) <= 2100:
                raise ValueError("Nieprawidłowy rok.")
            company = conn.execute("SELECT * FROM firmy WHERE id=%s", (firma_id,)).fetchone()
            if not company:
                raise ValueError("Nie znaleziono firmy.")
            owner = conn.execute("SELECT imie_pierwsze, nazwisko FROM udzialowcy WHERE firma_id=%s AND id=%s", (firma_id, owner_id)).fetchone()
            if not owner:
                raise ValueError("Nie znaleziono wspólnika tej firmy.")
            _seed_company_snapshot(conn, year, dict(company))
            name = " ".join(filter(None, owner)) or f"Wspólnik #{owner_id}"
            conn.execute("INSERT INTO owner_year_declarations(firma_id, rok, owner_id, owner_name, declarations_json) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (firma_id,rok,owner_id) DO UPDATE SET declarations_json=EXCLUDED.declarations_json", (firma_id, year, owner_id, name, json.dumps(codes)))
            conn.execute("INSERT INTO audit_log(tabela,rekord_id,akcja,uzytkownik_id,szczegoly) VALUES ('owner_year_declarations',%s,'edycja',%s,%s)", (firma_id, actor_id, json.dumps({"year":year,"owner_id":owner_id,"declarations":codes})))
