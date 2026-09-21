# app/db/dao_faktury.py
import json
import sqlite3
import logging
import os
import traceback
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta

from app.db.dao_conn import get_conn

# --- Paths for logs (absolute, project-root relative) ---
PROJECT_ROOT = os.path.abspath(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
ERROR_LOG_PATH = os.path.join(PROJECT_ROOT, "faktury_errors.log")
RUN_LOG_PATH = os.path.join(PROJECT_ROOT, "generate_invoices_run.log")

# Logger do błędów faktur (faktury_errors.log)
logger = logging.getLogger("dao_faktury")
if not logger.handlers:
    fh = logging.FileHandler(ERROR_LOG_PATH, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)
logger.setLevel(logging.INFO)

# Dedykowany logger dla przebiegu generowania (generate_invoices_run.log)
run_logger = logging.getLogger("generate_invoices")
if not run_logger.handlers:
    rfh = logging.FileHandler(RUN_LOG_PATH, encoding="utf-8")
    rfh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    run_logger.addHandler(rfh)
run_logger.setLevel(logging.DEBUG)


def _safe_commit(conn: sqlite3.Connection):
    try:
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise


def create_or_update_invoice(
    firma_id: int,
    miesiac: str,
    pozycje: List[Dict[str, Any]],
    kwota_netto: float,
    kwota_brutto: float,
    existing_invoice_id: Optional[int] = None
) -> int:
    """
    Tworzy lub aktualizuje fakturę. Zwraca id faktury.
    Podnosi ValueError przy walidacji, inne wyjątki są logowane i przepuszczane.
    """
    if not isinstance(miesiac, str) or len(miesiac) < 7:
        raise ValueError("Nieprawidłowy miesiąc. Oczekiwany format 'YYYY-MM'.")

    if not pozycje or not isinstance(pozycje, list):
        raise ValueError("Faktura musi zawierać co najmniej jedną pozycję.")

    conn = get_conn()
    cur = conn.cursor()

    pozycje_json = json.dumps(pozycje, ensure_ascii=False)
    today = datetime.now().strftime("%Y-%m-%d")
    termin = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    try:
        if existing_invoice_id:
            cur.execute("""
                UPDATE faktury
                SET kwota_netto = ?, kwota_brutto = ?, pozycje_json = ?,
                    data_wystawienia = ?, data_sprzedazy = ?, termin_platnosci = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (round(kwota_netto, 2), round(kwota_brutto, 2), pozycje_json, today, today, termin, existing_invoice_id))
            if cur.rowcount == 0:
                raise ValueError(f"Nie znaleziono faktury o id {existing_invoice_id}.")
            invoice_id = existing_invoice_id
        else:
            cur.execute("""
                INSERT INTO faktury (
                    firma_id, miesiac,
                    kwota_netto, kwota_brutto,
                    data_wystawienia, data_sprzedazy, termin_platnosci,
                    pozycje_json, eksportowana, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """, (firma_id, miesiac, round(kwota_netto, 2), round(kwota_brutto, 2),
                  today, today, termin, pozycje_json))
            invoice_id = cur.lastrowid

        _safe_commit(conn)
        run_logger.debug(f"Invoice saved: firma_id={firma_id}, miesiac={miesiac}, invoice_id={invoice_id}, netto={kwota_netto}, brutto={kwota_brutto}")
        return invoice_id
    except sqlite3.IntegrityError as e:
        conn.rollback()
        msg = f"SQLite IntegrityError podczas zapisu faktury dla firmy {firma_id}, miesiac={miesiac}: {e}"
        logger.exception(msg)
        run_logger.exception(msg)
        raise ValueError(msg)
    except ValueError:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        msg = f"Nieoczekiwany błąd podczas zapisu faktury dla firmy {firma_id}, miesiac={miesiac}: {e}"
        logger.exception(msg)
        run_logger.exception(msg)
        raise
    finally:
        conn.close()


def get_invoice_for_company_month(firma_id: int, miesiac: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM faktury WHERE firma_id = ? AND miesiac = ?", (firma_id, miesiac))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_invoices_for_company(firma_id: int) -> List[Dict[str, Any]]:
    """
    Zwraca listę faktur dla danej firmy (używane przez UI).
    Każdy element to słownik z kolumnami tabeli faktury.
    """
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM faktury
        WHERE firma_id = ?
        ORDER BY miesiac DESC, created_at DESC
    """, (firma_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_exported_invoices() -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT f.*, c.nazwa AS firma_nazwa
        FROM faktury f
        JOIN firmy c ON f.firma_id = c.id
        WHERE COALESCE(f.eksportowana, 0) = 1
        ORDER BY f.updated_at DESC, f.created_at DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_exported(invoice_ids: List[int]) -> None:
    if not invoice_ids:
        return
    conn = get_conn()
    cur = conn.cursor()
    placeholders = ",".join(["?"] * len(invoice_ids))
    cur.execute(f"UPDATE faktury SET eksportowana = 1, data_eksportu = CURRENT_TIMESTAMP WHERE id IN ({placeholders})", invoice_ids)
    conn.commit()
    conn.close()


# Archiwizacja i usuwanie z zachowaniem pozycji
def _ensure_archiwum_table_exists():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS faktury_archiwum (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            faktura_id INTEGER,
            firma_id INTEGER,
            miesiac TEXT,
            pozycje_json TEXT,
            kwota_netto REAL,
            kwota_brutto REAL,
            archived_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def archive_invoice_positions(conn: sqlite3.Connection, invoice_row: Dict[str, Any]) -> None:
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO faktury_archiwum (
            faktura_id, firma_id, miesiac, pozycje_json, kwota_netto, kwota_brutto, archived_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        invoice_row.get("id"),
        invoice_row.get("firma_id"),
        invoice_row.get("miesiac"),
        invoice_row.get("pozycje_json"),
        invoice_row.get("kwota_netto"),
        invoice_row.get("kwota_brutto"),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))


def delete_invoice_keep_positions(invoice_id: int) -> bool:
    _ensure_archiwum_table_exists()
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM faktury WHERE id = ?", (invoice_id,))
        row = cur.fetchone()
        if not row:
            return False
        invoice = dict(row)
        try:
            archive_invoice_positions(conn, invoice)
            cur.execute("DELETE FROM faktury WHERE id = ?", (invoice_id,))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def delete_invoices_for_company_month_keep_positions(firma_id: int, miesiac: str) -> int:
    _ensure_archiwum_table_exists()
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM faktury WHERE firma_id = ? AND miesiac = ?", (firma_id, miesiac))
        rows = cur.fetchall()
        if not rows:
            return 0
        deleted = 0
        try:
            for r in rows:
                fid = r["id"]
                cur.execute("SELECT * FROM faktury WHERE id = ?", (fid,))
                invoice_row = dict(cur.fetchone())
                archive_invoice_positions(conn, invoice_row)
                cur.execute("DELETE FROM faktury WHERE id = ?", (fid,))
                deleted += 1
            conn.commit()
            return deleted
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def generate_monthly_invoices_for_month(miesiac: str) -> Dict[str, Any]:
    """
    Generuje faktury dla wszystkich firm, które mają ustawione fakturowanie (fakturowac = 1).
    Zwraca raport z sukcesami i błędami:
    {
      "generated": [ (firma_id, invoice_id), ... ],
      "skipped": [ (firma_id, reason), ... ],
      "errors": [ (firma_id, error_message), ... ]
    }

    Ten wariant zapisuje szczegółowy log przebiegu do generate_invoices_run.log.
    """
    report = {"generated": [], "skipped": [], "errors": []}
    run_logger.info(f"Start generating invoices for month: {miesiac}")
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        # Najpierw spróbuj pobrać firmy z kolumną fakturowac (jeśli istnieje)
        firms = []
        try:
            cur.execute("SELECT id, nazwa, glowny_cennik, limit_dokumentow, zdrowotna, fakturowac, forma_dzialalnosci, vat_czynny FROM firmy")
            rows = cur.fetchall()
            run_logger.debug(f"Fetched {len(rows)} firms from DB (raw).")
            # convert to dicts
            for r in rows:
                firms.append(dict(r))
        except sqlite3.OperationalError as e:
            # jeśli kolumna nie istnieje lub inny błąd, pobierz minimalne pola i loguj
            run_logger.warning(f"Initial select failed: {e}. Falling back to safe select of id,nazwa,glowny_cennik,limit_dokumentow,zdrowotna.")
            try:
                cur.execute("SELECT id, nazwa, glowny_cennik, limit_dokumentow, zdrowotna FROM firmy")
                rows = cur.fetchall()
                run_logger.debug(f"Fetched {len(rows)} firms with fallback query.")
                for r in rows:
                    # row may be sqlite3.Row or tuple
                    if isinstance(r, sqlite3.Row):
                        firms.append(dict(r))
                    else:
                        # tuple fallback: map by position (id, nazwa, glowny_cennik, limit_dokumentow, zdrowotna)
                        firms.append({
                            "id": r[0],
                            "nazwa": r[1] if len(r) > 1 else None,
                            "glowny_cennik": r[2] if len(r) > 2 else None,
                            "limit_dokumentow": r[3] if len(r) > 3 else 0,
                            "zdrowotna": r[4] if len(r) > 4 else 0,
                            # fakturowac missing -> treat as 0
                            "fakturowac": 0
                        })
            except Exception as e2:
                run_logger.exception(f"Failed fallback select of firms: {e2}")
                report["errors"].append(("ALL", f"DB select failed: {e2}"))
                return report

        run_logger.info(f"Total firms to consider: {len(firms)}")

        for firma in firms:
            fid = firma.get("id")
            fname = firma.get("nazwa", "<brak nazwy>")
            run_logger.debug(f"Processing firma id={fid}, nazwa={fname}")

            try:
                # Determine fakturowac flag robustly
                fakturowac_flag = False
                try:
                    # prefer explicit column if present
                    if "fakturowac" in firma:
                        fakturowac_flag = bool(firma.get("fakturowac", 0))
                    else:
                        # try to read from DB single row (safe)
                        c2 = conn.cursor()
                        c2.execute("PRAGMA table_info(firmy)")
                        cols = [c[1] for c in c2.fetchall()]
                        if "fakturowac" in cols:
                            c2.execute("SELECT COALESCE(fakturowac,0) FROM firmy WHERE id = ?", (fid,))
                            rr = c2.fetchone()
                            if rr:
                                fakturowac_flag = bool(rr[0])
                        else:
                            fakturowac_flag = False
                except Exception as e:
                    run_logger.warning(f"Could not determine fakturowac for firma {fid}: {e}")
                    fakturowac_flag = False

                # Check if invoice already exists for this month
                cur2 = conn.cursor()
                cur2.execute("SELECT id FROM faktury WHERE firma_id = ? AND miesiac = ?", (fid, miesiac))
                existing = cur2.fetchone()
                if existing:
                    run_logger.info(f"Firma {fid} ({fname}): skipped - invoice already exists (id={existing[0]})")
                    report["skipped"].append((fid, "Faktura już istnieje"))
                    continue

                # If fakturowac_flag True -> full invoice
                if fakturowac_flag:
                    run_logger.debug(f"Firma {fid} ({fname}): fakturowac=1 -> generating full invoice")
                    # choose cennik key
                    klucz = firma.get("glowny_cennik")
                    if not klucz:
                        # try to pick automatically
                        try:
                            cur3 = conn.cursor()
                            cur3.execute("SELECT klucz, cena_netto_ksiegowosc, zdrowotna, netto, brutto FROM cenniki")
                            crows = cur3.fetchall()
                            cenniki_rows = []
                            for r in crows:
                                # r may be tuple or Row
                                if isinstance(r, sqlite3.Row):
                                    cenniki_rows.append({
                                        "klucz": r["klucz"],
                                        "cena_netto_ksiegowosc": r["cena_netto_ksiegowosc"],
                                        "zdrowotna": r["zdrowotna"],
                                        "netto": r["netto"],
                                        "brutto": r["brutto"]
                                    })
                                else:
                                    cenniki_rows.append({
                                        "klucz": r[0],
                                        "cena_netto_ksiegowosc": r[1],
                                        "zdrowotna": r[2],
                                        "netto": r[3],
                                        "brutto": r[4]
                                    })
                            try:
                                from app.companies.company_logic import build_cennik_key, pick_cennik_by_key_or_limit
                                desired = build_cennik_key(firma.get("forma_dzialalnosci") or "KPiR",
                                                          bool(firma.get("vat_czynny", 0)),
                                                          int(firma.get("limit_dokumentow") or 0))
                                chosen = pick_cennik_by_key_or_limit(desired, int(firma.get("limit_dokumentow") or 0), cenniki_rows)
                                if chosen:
                                    klucz = chosen.get("klucz")
                                    run_logger.debug(f"Firma {fid}: auto-chosen cennik {klucz} for desired {desired}")
                            except Exception as e:
                                run_logger.debug(f"Firma {fid}: cannot auto-pick cennik helper: {e}")
                        except Exception as e:
                            run_logger.exception(f"Firma {fid}: error fetching cenniki for auto-pick: {e}")

                    # fetch cennik values
                    ccur = conn.cursor()
                    ccur.execute("SELECT cena_netto_ksiegowosc, zdrowotna, netto FROM cenniki WHERE klucz = ?", (klucz,))
                    crow = ccur.fetchone()
                    if not crow:
                        run_logger.warning(f"Firma {fid} ({fname}): brak cennika dla klucza '{klucz}' -> skipping")
                        report["errors"].append((fid, f"Brak cennika dla klucza '{klucz}'"))
                        continue

                    # crow may be Row or tuple
                    if isinstance(crow, sqlite3.Row):
                        cena_netto_ks = float(crow["cena_netto_ksiegowosc"] or 0.0)
                        zdrowotna_val = float(crow["zdrowotna"] or 0.0)
                    else:
                        cena_netto_ks = float(crow[0] or 0.0)
                        zdrowotna_val = float(crow[1] or 0.0)

                    pozycje = []
                    fakturowac_zdrowotna_w_cenie = bool(firma.get("zdrowotna", 0))
                    if fakturowac_zdrowotna_w_cenie:
                        pozycje.append({
                            "opis": "Księgowość (miesięczna) - podstawowa opłata",
                            "ilosc": 1,
                            "cena_netto": round(cena_netto_ks, 2),
                            "stawka_vat": "0",
                            "wartosc_netto": round(cena_netto_ks, 2),
                            "wartosc_brutto": round(cena_netto_ks * 1.23, 2)
                        })
                        total_netto = round(cena_netto_ks, 2)
                        total_brutto = round(cena_netto_ks * 1.23, 2)
                    else:
                        base_netto = float(cena_netto_ks or 0.0)
                        pozycje.append({
                            "opis": "Księgowość (miesięczna) - podstawa",
                            "ilosc": 1,
                            "cena_netto": round(base_netto, 2),
                            "stawka_vat": "0",
                            "wartosc_netto": round(base_netto, 2),
                            "wartosc_brutto": round(base_netto * 1.23, 2)
                        })
                        if zdrowotna_val and zdrowotna_val > 0:
                            pozycje.append({
                                "opis": "Składka zdrowotna (miesięczna)",
                                "ilosc": 1,
                                "cena_netto": round(zdrowotna_val, 2),
                                "stawka_vat": "0",
                                "wartosc_netto": round(zdrowotna_val, 2),
                                "wartosc_brutto": round(zdrowotna_val * 1.23, 2)
                            })
                        total_netto = round(sum(p["wartosc_netto"] for p in pozycje), 2)
                        total_brutto = round(sum(p["wartosc_brutto"] for p in pozycje), 2)

                    invoice_id = create_or_update_invoice(fid, miesiac, pozycje, total_netto, total_brutto)
                    run_logger.info(f"Firma {fid} ({fname}): generated invoice id={invoice_id}, netto={total_netto}, brutto={total_brutto}")
                    report["generated"].append((fid, invoice_id))

                else:
                    # Only additional services: generate positions and filter out księgowość-related ones
                    run_logger.debug(f"Firma {fid} ({fname}): fakturowac=0 -> generating only additional services")
                    # Build positions using existing helper logic in this module is limited;
                    # we'll compute additional services from uslugi_dodatkowe_firma and statusy_miesieczne
                    try:
                        # uslugi_dodatkowe_firma
                        cur_extra = conn.cursor()
                        cur_extra.execute("""
                            SELECT u.nazwa, u.kategoria, u.stawka_vat, uf.ilosc, uf.cena_netto
                            FROM uslugi_dodatkowe_firma uf
                            JOIN uslugi_dodatkowe u ON uf.usluga_id = u.id
                            WHERE uf.firma_id = ? AND uf.miesiac = ?
                            ORDER BY u.nazwa
                        """, (fid, miesiac))
                        extras = cur_extra.fetchall()
                        filtered = []
                        for ex in extras:
                            # ex may be Row or tuple
                            if isinstance(ex, sqlite3.Row):
                                nazwa = ex["nazwa"]
                                kategoria = ex["kategoria"]
                                stawka_vat = ex["stawka_vat"]
                                ilosc = int(ex["ilosc"] or 0)
                                cena_netto = float(ex["cena_netto"] or 0.0)
                            else:
                                nazwa = ex[0]
                                kategoria = ex[1]
                                stawka_vat = ex[2]
                                ilosc = int(ex[3] or 0)
                                cena_netto = float(ex[4] or 0.0)
                            if ilosc == 0:
                                continue
                            netto = round(cena_netto * ilosc, 2)
                            try:
                                vat = int(float(str(stawka_vat).replace("%", "").strip()))
                            except Exception:
                                vat = 0
                            brutto = round(netto * (1 + vat / 100), 2)
                            filtered.append({
                                "nazwa": nazwa,
                                "ilosc": ilosc,
                                "jednostka": "szt.",
                                "netto": netto,
                                "vat": vat,
                                "brutto": brutto,
                                "kategoria": kategoria,
                                "generated": True
                            })

                        # praca stoper (statusy_miesieczne)
                        cur_status = conn.cursor()
                        cur_status.execute("SELECT sekundy_ksiegowosc, sekundy_kadry, ilosc_dok FROM statusy_miesieczne WHERE firma_id = ? AND miesiac = ?", (fid, miesiac))
                        st = cur_status.fetchone()
                        if st:
                            if isinstance(st, sqlite3.Row):
                                sek_ks = int(st["sekundy_ksiegowosc"] or 0)
                                sek_kd = int(st["sekundy_kadry"] or 0)
                            else:
                                sek_ks = int(st[0] or 0)
                                sek_kd = int(st[1] or 0)
                            if sek_kd > 0:
                                godz = sek_kd / 3600
                                netto = round(godz * 120, 2)
                                brutto = round(netto * 1.23, 2)
                                filtered.append({
                                    "nazwa": "Praca kadrowa",
                                    "ilosc": round(godz, 2),
                                    "jednostka": "godz.",
                                    "netto": netto,
                                    "vat": 23,
                                    "brutto": brutto,
                                    "kategoria": "kadry",
                                    "generated": True
                                })
                        # If nothing to add, skip
                        if not filtered:
                            run_logger.info(f"Firma {fid} ({fname}): no additional positions to add for month {miesiac}")
                            report["skipped"].append((fid, "Brak dodatkowych usług"))
                            continue

                        # Merge into existing invoice or create new
                        cur_exist = conn.cursor()
                        cur_exist.execute("SELECT * FROM faktury WHERE firma_id = ? AND miesiac = ?", (fid, miesiac))
                        existing = cur_exist.fetchone()
                        if existing:
                            try:
                                existing_positions = json.loads(existing.get("pozycje_json") or "[]") if isinstance(existing, sqlite3.Row) else json.loads(existing[7] or "[]")
                            except Exception:
                                existing_positions = []
                            # simple merge: append filtered (could use _merge_positions from logic module)
                            merged = existing_positions + filtered
                            kw_net = round(sum(float(p.get("netto") or 0) for p in merged), 2)
                            kw_br = round(sum(float(p.get("brutto") or 0) for p in merged), 2)
                            invoice_id = create_or_update_invoice(
                                firma_id=fid,
                                miesiac=miesiac,
                                pozycje=merged,
                                kwota_netto=kw_net,
                                kwota_brutto=kw_br,
                                existing_invoice_id=existing["id"] if isinstance(existing, sqlite3.Row) else existing[0]
                            )
                            run_logger.info(f"Firma {fid} ({fname}): merged additional positions into existing invoice id={invoice_id}")
                            report["generated"].append((fid, invoice_id))
                        else:
                            kw_net = round(sum(float(p.get("netto") or 0) for p in filtered), 2)
                            kw_br = round(sum(float(p.get("brutto") or 0) for p in filtered), 2)
                            invoice_id = create_or_update_invoice(
                                firma_id=fid,
                                miesiac=miesiac,
                                pozycje=filtered,
                                kwota_netto=kw_net,
                                kwota_brutto=kw_br,
                                existing_invoice_id=None
                            )
                            run_logger.info(f"Firma {fid} ({fname}): created invoice with additional positions id={invoice_id}")
                            report["generated"].append((fid, invoice_id))

                    except Exception as e:
                        tb = traceback.format_exc()
                        run_logger.exception(f"Firma {fid} ({fname}): error while generating additional services: {e}\n{tb}")
                        report["errors"].append((fid, str(e)))
                        continue

            except Exception as e:
                tb = traceback.format_exc()
                run_logger.exception(f"Firma {fid} ({fname}): unexpected error: {e}\n{tb}")
                report["errors"].append((fid, str(e)))
                continue
            # debug: also log to stdout so PyCharm Run console shows messages immediately
            import sys
            if not any(isinstance(h, logging.StreamHandler) for h in run_logger.handlers):
                sh = logging.StreamHandler(sys.stdout)
                sh.setLevel(logging.DEBUG)
                sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
                run_logger.addHandler(sh)

        run_logger.info(f"Finished generating invoices for month: {miesiac}. Generated: {len(report['generated'])}, Skipped: {len(report['skipped'])}, Errors: {len(report['errors'])}")

        return report
    finally:
        conn.close()
