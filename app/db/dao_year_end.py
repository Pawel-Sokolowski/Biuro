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


def list_year_end(year: int) -> List[Dict]:
    """Use a durable snapshot of each year's company obligations."""
    conn = get_conn()
    try:
        conn.execute("SELECT pg_advisory_xact_lock(748204)")
        exists = conn.execute("SELECT 1 FROM year_end_snapshot_years WHERE rok = %s", (year,)).fetchone()
        if not exists:
            # Include inactive companies with previously recorded filings too.
            companies = conn.execute("""
                SELECT id, nazwa, nazwa_skrocona, nip, forma_opodatkowania, forma_dzialalnosci,
                       rozliczenie_roczne, deklaracje_dodatkowe, roczne_deklaracje FROM firmy
                WHERE COALESCE(status, 'Aktywne') = 'Aktywne'
                   OR id IN (SELECT firma_id FROM zakonczenie_roku WHERE rok = %s)
            """, (year,)).fetchall()
            for row in companies:
                company = dict(row)
                codes = company_declarations(company)
                for saved_code in conn.execute("SELECT deklaracja FROM zakonczenie_roku WHERE firma_id = %s AND rok = %s AND owner_key = 0", (company["id"], year)).fetchall():
                    if saved_code[0] not in codes:
                        codes.append(saved_code[0])
                company["declaration_codes"] = codes
                conn.execute("INSERT INTO year_end_snapshots(rok, firma_id, company_json) VALUES (%s, %s, %s) ON CONFLICT (rok, firma_id) DO NOTHING", (year, company["id"], json.dumps(company, ensure_ascii=False)))
            conn.execute("INSERT INTO year_end_snapshot_years(rok) VALUES (%s)", (year,))
        companies = [json.loads(row[0]) for row in conn.execute("SELECT company_json FROM year_end_snapshots WHERE rok = %s ORDER BY firma_id", (year,)).fetchall()]
        owners = [dict(r) for r in conn.execute("SELECT * FROM owner_year_declarations WHERE rok = %s", (year,)).fetchall()]
        conn.commit()
        saved = {}
        for row in conn.execute("SELECT * FROM zakonczenie_roku WHERE rok = %s", (year,)).fetchall():
            saved[(row["firma_id"], row["deklaracja"], row["owner_key"]) ] = dict(row)
    finally:
        conn.close()
    result = []
    for company in companies:
        company = dict(company)
        declarations = []
        for code in company.get("declaration_codes", company_declarations(company)):
            declarations.append({"kod": code, **saved.get((company["id"], code, 0), {})})
        owner_rows = [r for r in owners if r["firma_id"] == company["id"]]
        owner_codes = {code for r in owner_rows for code in json.loads(r["declarations_json"])}
        declarations = [d for d in declarations if d["kod"] not in owner_codes or (company["id"], d["kod"], 0) in saved]
        for owner in owner_rows:
            codes = json.loads(owner["declarations_json"])
            codes += [code for (fid, code, oid) in saved if fid == company["id"] and oid == owner["owner_id"] and code not in codes]
            for code in codes:
                declarations.append({"kod": code, "owner_key": owner["owner_id"], "owner_name": owner["owner_name"], **saved.get((company["id"], code, owner["owner_id"]), {})})
        company["deklaracje"] = declarations
        result.append(company)
    return result


def set_company_declarations(firma_id: int, main: str, extra: str, *, year: int, actor_id: int) -> None:
    list_year_end(year)  # Ensure the initial year snapshot exists.
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
    list_year_end(year)
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
            snapshot = json.loads(row[0]) if row else {k: company[k] for k in ("id", "nazwa", "nazwa_skrocona", "nip", "forma_opodatkowania", "forma_dzialalnosci")}
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
            company = conn.execute("SELECT id,nazwa,nazwa_skrocona,nip,forma_opodatkowania,forma_dzialalnosci FROM firmy WHERE id=%s", (firma_id,)).fetchone()
            if not company:
                raise ValueError("Nie znaleziono firmy.")
            snapshot = dict(company)
            snapshot["declaration_codes"] = []
            conn.execute("INSERT INTO year_end_snapshots(rok,firma_id,company_json) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (year,firma_id,json.dumps(snapshot,ensure_ascii=False)))
            owner = conn.execute("SELECT imie_pierwsze, nazwisko FROM udzialowcy WHERE firma_id=%s AND id=%s", (firma_id, owner_id)).fetchone()
            if not owner:
                raise ValueError("Nie znaleziono wspólnika tej firmy.")
            name = " ".join(filter(None, owner)) or f"Wspólnik #{owner_id}"
            conn.execute("INSERT INTO owner_year_declarations(firma_id, rok, owner_id, owner_name, declarations_json) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (firma_id,rok,owner_id) DO UPDATE SET declarations_json=EXCLUDED.declarations_json", (firma_id, year, owner_id, name, json.dumps(codes)))
            conn.execute("INSERT INTO audit_log(tabela,rekord_id,akcja,uzytkownik_id,szczegoly) VALUES ('owner_year_declarations',%s,'edycja',%s,%s)", (firma_id, actor_id, json.dumps({"year":year,"owner_id":owner_id,"declarations":codes})))
