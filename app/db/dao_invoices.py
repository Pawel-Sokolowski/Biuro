import sqlite3
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

from app.db.dao_conn import get_conn

DATE_FMT = "%Y-%m-%d"
DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


def _now_date() -> str:
    return datetime.now().strftime(DATE_FMT)


def _now_datetime() -> str:
    return datetime.now().strftime(DATETIME_FMT)


def _safe_commit(conn: sqlite3.Connection):
    try:
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return dict(row)


def get_invoice_by_id(invoice_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM faktury WHERE id = ?", (invoice_id,))
        row = cur.fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def get_invoice_for_company_month(firma_id: int, miesiac: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM faktury WHERE firma_id = ? AND miesiac = ?", (firma_id, miesiac))
        row = cur.fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def list_invoices_for_company(firma_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM faktury WHERE firma_id = ? ORDER BY miesiac DESC, created_at DESC",
            (firma_id,),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_exported_invoices() -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT f.*, c.nazwa AS firma_nazwa
            FROM faktury f
            JOIN firmy c ON f.firma_id = c.id
            WHERE COALESCE(f.eksportowana, 0) = 1
            ORDER BY f.updated_at DESC, f.created_at DESC
        """
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _get_or_create_draft_invoice_for_company(
    firma_id: int, miesiac: str, pracownik_id: Optional[int] = None
) -> int:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id FROM faktury WHERE firma_id = ? AND miesiac = ? AND COALESCE(status, '') = 'robocza' LIMIT 1",
            (firma_id, miesiac),
        )
        row = cur.fetchone()
        if row:
            return dict(row)["id"]

        today = _now_date()
        termin = (datetime.now() + timedelta(days=7)).strftime(DATE_FMT)
        cur.execute(
            """
            INSERT INTO faktury (
                firma_id, miesiac, kwota_netto, kwota_brutto,
                data_wystawienia, data_sprzedazy, termin_platnosci,
                pozycje_json, eksportowana, status, created_at, updated_at
            ) VALUES (?, ?, 0, 0, ?, ?, ?, ?, 0, 'robocza', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (firma_id, miesiac, today, today, termin, "[]"),
        )
        invoice_id = cur.lastrowid
        conn.commit()
        return invoice_id
    finally:
        conn.close()


def _recalculate_invoice_totals(conn: sqlite3.Connection, invoice_id: int):
    cur = conn.cursor()
    cur.execute(
        "SELECT ilosc, cena_netto, wartosc_netto FROM faktury_pozycje WHERE invoice_id = ?",
        (invoice_id,),
    )
    rows = cur.fetchall()
    total_netto = 0.0
    for r in rows:
        if isinstance(r, sqlite3.Row):
            wart_net = r["wartosc_netto"] if r["wartosc_netto"] is not None else 0.0
        else:
            wart_net = r[2] if len(r) > 2 and r[2] is not None else 0.0
        total_netto += float(wart_net)
    total_netto = round(total_netto, 2)
    total_brutto = round(total_netto * 1.23, 2)
    cur.execute(
        "UPDATE faktury SET kwota_netto = ?, kwota_brutto = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (total_netto, total_brutto, invoice_id),
    )


def add_invoice_item_for_service(
    firma_id: int,
    usluga_id: int,
    ilosc_hours: float,
    miesiac: str,
    pracownik_id: Optional[int] = None,
) -> Optional[int]:
    if ilosc_hours is None or ilosc_hours <= 0:
        return None

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, nazwa, cena_netto, stawka_vat FROM uslugi_dodatkowe WHERE id = ?",
            (usluga_id,),
        )
        svc_row = cur.fetchone()
        if not svc_row:
            return None
        svc = dict(svc_row)
        cena_netto = float(svc.get("cena_netto") or 0.0)
        nazwa = svc.get("nazwa") or "Usługa dodatkowa"
        stawka_vat = svc.get("stawka_vat") if svc.get("stawka_vat") is not None else "23"

        invoice_id = _get_or_create_draft_invoice_for_company(firma_id, miesiac, pracownik_id)

        ilosc_rounded = round(float(ilosc_hours), 2)
        wartosc_netto = round(cena_netto * ilosc_rounded, 2)

        cur.execute(
            """
            INSERT INTO faktury_pozycje (
                invoice_id, usluga_id, nazwa, ilosc, cena_netto, wartosc_netto, stawka_vat, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invoice_id,
                usluga_id,
                nazwa,
                ilosc_rounded,
                round(cena_netto, 2),
                wartosc_netto,
                str(stawka_vat),
                _now_datetime(),
            ),
        )

        _recalculate_invoice_totals(conn, invoice_id)

        conn.commit()
        return invoice_id

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def add_custom_invoice_item(
    invoice_id: int,
    opis: str,
    ilosc: float,
    cena_netto: float,
    stawka_vat: str = "23",
) -> bool:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        ilosc_r = round(float(ilosc), 2)
        cena_r = round(float(cena_netto), 2)
        wart_net = round(ilosc_r * cena_r, 2)
        cur.execute(
            """
            INSERT INTO faktury_pozycje (
                invoice_id, usluga_id, nazwa, ilosc, cena_netto, wartosc_netto, stawka_vat, created_at
            ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (invoice_id, opis, ilosc_r, cena_r, wart_net, str(stawka_vat), _now_datetime()),
        )
        _recalculate_invoice_totals(conn, invoice_id)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_or_update_invoice(
    firma_id: int,
    miesiac: str,
    pozycje: List[Dict[str, Any]],
    kwota_netto: float,
    kwota_brutto: float,
    existing_invoice_id: Optional[int] = None,
) -> int:
    if not isinstance(miesiac, str) or len(miesiac) < 7:
        raise ValueError("Nieprawidłowy miesiąc. Oczekiwany format 'YYYY-MM'.")

    if not pozycje or not isinstance(pozycje, list):
        raise ValueError("Faktura musi zawierać co najmniej jedną pozycję.")

    conn = get_conn()
    cur = conn.cursor()

    pozycje_json = json.dumps(pozycje, ensure_ascii=False)
    today = _now_date()
    termin = (datetime.now() + timedelta(days=7)).strftime(DATE_FMT)

    try:
        if existing_invoice_id:
            cur.execute(
                """
                UPDATE faktury
                SET kwota_netto = ?, kwota_brutto = ?, pozycje_json = ?,
                    data_wystawienia = ?, data_sprzedazy = ?, termin_platnosci = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """,
                (
                    round(kwota_netto, 2),
                    round(kwota_brutto, 2),
                    pozycje_json,
                    today,
                    today,
                    termin,
                    existing_invoice_id,
                ),
            )

            if cur.rowcount == 0:
                raise ValueError(f"Nie znaleziono faktury o id {existing_invoice_id}.")

            invoice_id = existing_invoice_id

        else:
            cur.execute(
                """
                INSERT INTO faktury (
                    firma_id, miesiac,
                    kwota_netto, kwota_brutto,
                    data_wystawienia, data_sprzedazy, termin_platnosci,
                    pozycje_json, eksportowana, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
                (
                    firma_id,
                    miesiac,
                    round(kwota_netto, 2),
                    round(kwota_brutto, 2),
                    today,
                    today,
                    termin,
                    pozycje_json,
                ),
            )

            invoice_id = cur.lastrowid

        _safe_commit(conn)
        return invoice_id

    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise ValueError(
            f"SQLite IntegrityError podczas zapisu faktury dla firmy {firma_id}, miesiac={miesiac}: {e}"
        )

    except ValueError:
        conn.rollback()
        raise

    except Exception as e:
        conn.rollback()
        raise

    finally:
        conn.close()


def mark_exported(invoice_ids: List[int]):
    if not invoice_ids:
        return
    conn = get_conn()
    cur = conn.cursor()
    try:
        placeholders = ",".join(["?"] * len(invoice_ids))
        cur.execute(
            f"UPDATE faktury SET eksportowana = 1, data_eksportu = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
            invoice_ids,
        )
        conn.commit()
    finally:
        conn.close()


def _ensure_archiwum_table_exists():
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
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
        """
        )
        conn.commit()
    finally:
        conn.close()


def archive_invoice_positions(conn: sqlite3.Connection, invoice_row: Dict[str, Any]):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO faktury_archiwum (
            faktura_id, firma_id, miesiac, pozycje_json, kwota_netto, kwota_brutto, archived_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
        (
            invoice_row.get("id"),
            invoice_row.get("firma_id"),
            invoice_row.get("miesiac"),
            invoice_row.get("pozycje_json"),
            invoice_row.get("kwota_netto"),
            invoice_row.get("kwota_brutto"),
            _now_datetime(),
        ),
    )


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
        archive_invoice_positions(conn, invoice)

        cur.execute("DELETE FROM faktury_pozycje WHERE invoice_id = ?", (invoice_id,))
        cur.execute("DELETE FROM faktury WHERE id = ?", (invoice_id,))
        conn.commit()
        return True

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def generate_monthly_invoices_for_month(miesiac: str) -> Dict[str, List[Tuple[int, Any]]]:
    report = {"generated": [], "skipped": [], "errors": []}

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT id, nazwa, glowny_cennik, limit_dokumentow, zdrowotna, fakturowac, forma_dzialalnosci, vat_czynny FROM firmy"
        )
        firms_rows = cur.fetchall()
        firms = [dict(r) for r in firms_rows]

        for firma in firms:
            fid = firma["id"]
            fakturowac_flag = bool(firma.get("fakturowac", 0))

            cur2 = conn.cursor()
            cur2.execute("SELECT id FROM faktury WHERE firma_id = ? AND miesiac = ?", (fid, miesiac))
            if cur2.fetchone():
                report["skipped"].append((fid, "Faktura już istnieje"))
                continue

            if not fakturowac_flag:
                report["skipped"].append((fid, "fakturowac=0"))
                continue

            klucz = firma.get("glowny_cennik")
            ccur = conn.cursor()
            ccur.execute(
                "SELECT cena_netto_ksiegowosc, zdrowotna, netto FROM cenniki WHERE klucz = ?",
                (klucz,),
            )
            crow_row = ccur.fetchone()
            if not crow_row:
                report["errors"].append((fid, f"Brak cennika dla klucza '{klucz}'"))
                continue
            crow = dict(crow_row)

            cena_netto_ks = float(crow.get("cena_netto_ksiegowosc") or 0.0)
            zdrowotna_val = float(crow.get("zdrowotna") or 0.0)
            netto_total = float(crow.get("netto") or 0.0)

            pozycje: List[Dict[str, Any]] = []
            wliczaj_zdrowotna = bool(firma.get("zdrowotna", 0))

            if wliczaj_zdrowotna:
                pozycje.append(
                    {
                        "opis": "Księgowość (miesięczna) - podstawowa opłata",
                        "ilosc": 1,
                        "cena_netto": round(netto_total, 2),
                        "stawka_vat": "0",
                        "wartosc_netto": round(netto_total, 2),
                        "wartosc_brutto": round(netto_total * 1.23, 2),
                    }
                )
                total_netto = round(netto_total, 2)
                total_brutto = round(netto_total * 1.23, 2)
            else:
                pozycje.append(
                    {
                        "opis": "Księgowość (miesięczna) - podstawa",
                        "ilosc": 1,
                        "cena_netto": round(cena_netto_ks, 2),
                        "stawka_vat": "0",
                        "wartosc_netto": round(cena_netto_ks, 2),
                        "wartosc_brutto": round(cena_netto_ks * 1.23, 2),
                    }
                )
                if zdrowotna_val > 0:
                    pozycje.append(
                        {
                            "opis": "Składka zdrowotna (miesięczna)",
                            "ilosc": 1,
                            "cena_netto": round(zdrowotna_val, 2),
                            "stawka_vat": "0",
                            "wartosc_netto": round(zdrowotna_val, 2),
                            "wartosc_brutto": round(zdrowotna_val * 1.23, 2),
                        }
                    )
                total_netto = round(sum(p["wartosc_netto"] for p in pozycje), 2)
                total_brutto = round(sum(p["wartosc_brutto"] for p in pozycje), 2)

            invoice_id = create_or_update_invoice(fid, miesiac, pozycje, total_netto, total_brutto)
            report["generated"].append((fid, invoice_id))

        return report

    finally:
        conn.close()
