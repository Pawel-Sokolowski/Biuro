import sqlite3
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

from app.db.dao_conn import get_conn
from app.invoices.positions import normalize_position, reverse_positions, totals
from app.security.authorization import assigned_company_ids, can, is_administrator

DATE_FMT = "%Y-%m-%d"
DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


class InvoiceLockedError(ValueError):
    """Próba zmiany faktury, która została już wystawiona (wyeksportowana)."""


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
        cur.execute(
            "SELECT * FROM faktury WHERE firma_id = ? AND miesiac = ? AND rodzaj = 'faktura'",
            (firma_id, miesiac),
        )
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


def get_exported_invoices(pracownik_id: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        sql = """
            SELECT f.*, c.nazwa AS firma_nazwa
            FROM faktury f
            JOIN firmy c ON f.firma_id = c.id
            WHERE COALESCE(f.eksportowana, 0) = 1
        """
        params: List[Any] = []
        if pracownik_id and not (
            is_administrator(pracownik_id) or can(pracownik_id, "can_view_finances")
        ):
            ids = assigned_company_ids(pracownik_id)
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            sql += f" AND f.firma_id IN ({placeholders})"
            params.extend(sorted(ids))
        cur.execute(sql + " ORDER BY f.updated_at DESC, f.created_at DESC", params)
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
            """
            SELECT id
            FROM faktury
            WHERE firma_id = ?
              AND miesiac = ?
              AND COALESCE(eksportowana, 0) = 0
              AND rodzaj = 'faktura'
            ORDER BY CASE WHEN COALESCE(status, '') = 'robocza' THEN 0 ELSE 1 END,
                     updated_at DESC,
                     id DESC
            LIMIT 1
            """,
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
    cena_netto: Optional[float] = None,
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
        cena_netto = (
            float(cena_netto)
            if cena_netto is not None
            else float(svc.get("cena_netto") or 0.0)
        )
        nazwa = svc.get("nazwa") or "Usługa dodatkowa"
        stawka_vat = svc.get("stawka_vat") if svc.get("stawka_vat") is not None else "23"

        invoice_id = _get_or_create_draft_invoice_for_company(firma_id, miesiac, pracownik_id)

        ilosc_rounded = round(float(ilosc_hours), 2)
        wartosc_netto = round(cena_netto * ilosc_rounded, 2)
        vat_percent = float(str(stawka_vat).replace("%", "").strip() or 0)
        wartosc_brutto = round(wartosc_netto * (1 + vat_percent / 100), 2)

        cur.execute("SELECT pozycje_json FROM faktury WHERE id = ?", (invoice_id,))
        invoice_row = cur.fetchone()
        try:
            positions = json.loads(invoice_row["pozycje_json"] or "[]") if invoice_row else []
        except (TypeError, json.JSONDecodeError):
            positions = []

        positions.append(
            {
                "nazwa": nazwa,
                "ilosc": ilosc_rounded,
                "jednostka": "godz.",
                "netto": wartosc_netto,
                "cena_netto": round(cena_netto, 2),
                "vat": int(vat_percent),
                "brutto": wartosc_brutto,
                "kategoria": svc.get("kategoria") or "usługi dodatkowe",
                "usluga_id": usluga_id,
                "generated": False,
            }
        )

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

        cur.execute(
            """
            UPDATE faktury
            SET kwota_netto = ?, kwota_brutto = ?,
                pozycje_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                round(sum(float(p.get("netto") or 0) for p in positions), 2),
                round(sum(float(p.get("brutto") or 0) for p in positions), 2),
                json.dumps(positions, ensure_ascii=False),
                invoice_id,
            ),
        )

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
            locked = cur.execute(
                "SELECT COALESCE(eksportowana, 0) FROM faktury WHERE id = ?", (existing_invoice_id,)
            ).fetchone()
            if locked and locked[0]:
                raise InvoiceLockedError(
                    "Faktura została już wystawiona. Zmiany wprowadź przez korektę."
                )
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


def mark_exported(invoice_ids: List[int], pracownik_id: Optional[int] = None):
    if not invoice_ids:
        return
    conn = get_conn()
    cur = conn.cursor()
    try:
        placeholders = ",".join(["?"] * len(invoice_ids))
        params: List[Any] = list(invoice_ids)
        sql = (
            f"UPDATE faktury SET eksportowana = 1, status = 'wystawiona', data_eksportu = CURRENT_TIMESTAMP "
            f"WHERE id IN ({placeholders})"
        )
        if pracownik_id and not (
            is_administrator(pracownik_id) or can(pracownik_id, "can_export_data")
        ):
            sql += (
                " AND EXISTS ("
                "SELECT 1 FROM pracownik_firma pf "
                "WHERE pf.firma_id = faktury.firma_id AND pf.pracownik_id = ?)"
            )
            params.append(pracownik_id)
        cur.execute(sql, params)
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


# ---------------------------------------------------------------------------
# Lista, edycja, korekty i KSeF
# ---------------------------------------------------------------------------
INVOICE_LIST_SQL = """
    SELECT f.id, f.firma_id, c.nazwa AS firma_nazwa, f.miesiac, f.rodzaj, f.numer, f.status,
           f.kwota_netto, f.kwota_brutto, f.data_wystawienia, f.data_sprzedazy,
           f.termin_platnosci, f.eksportowana, f.data_eksportu, f.ksef_numer,
           f.korygowana_faktura_id, f.przyczyna_korekty
    FROM faktury f
    JOIN firmy c ON c.id = f.firma_id
"""


def _has_full_finance_access(pracownik_id: Optional[int]) -> bool:
    return bool(pracownik_id) and (
        is_administrator(pracownik_id) or can(pracownik_id, "can_view_finances")
    )


def can_access_invoice(pracownik_id: Optional[int], firma_id: int) -> bool:
    return _has_full_finance_access(pracownik_id) or firma_id in assigned_company_ids(pracownik_id)


def list_invoices(
    pracownik_id: Optional[int],
    wystawione: bool,
    miesiac: Optional[str] = None,
    firma_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Faktury robocze (wystawione=False) albo wystawione (wystawione=True) widoczne dla pracownika."""
    where = ["COALESCE(f.eksportowana, 0) = ?"]
    params: List[Any] = [1 if wystawione else 0]
    if miesiac:
        where.append("f.miesiac = ?")
        params.append(miesiac)
    if firma_id:
        where.append("f.firma_id = ?")
        params.append(firma_id)
    if not _has_full_finance_access(pracownik_id):
        ids = sorted(assigned_company_ids(pracownik_id))
        if not ids:
            return []
        where.append(f"f.firma_id IN ({','.join('?' for _ in ids)})")
        params.extend(ids)
    order = "f.data_eksportu DESC, f.id DESC" if wystawione else "c.nazwa COLLATE NOCASE, f.id"
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(f"{INVOICE_LIST_SQL} WHERE {' AND '.join(where)} ORDER BY {order}", params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_invoice_positions(invoice_id: int) -> List[Dict[str, Any]]:
    invoice = get_invoice_by_id(invoice_id)
    if not invoice:
        return []
    try:
        positions = json.loads(invoice.get("pozycje_json") or "[]")
    except (TypeError, ValueError):
        return []
    return positions if isinstance(positions, list) else []


def update_invoice_draft(
    invoice_id: int,
    pozycje: List[Dict[str, Any]],
    numer: Optional[str] = None,
    data_wystawienia: Optional[str] = None,
    data_sprzedazy: Optional[str] = None,
    termin_platnosci: Optional[str] = None,
    przyczyna_korekty: Optional[str] = None,
) -> Tuple[float, float]:
    """Zapisuje pozycje i nagłówek roboczej faktury; zwraca (netto, brutto)."""
    invoice = get_invoice_by_id(invoice_id)
    if not invoice:
        raise ValueError(f"Nie znaleziono faktury o id {invoice_id}.")
    if invoice.get("eksportowana"):
        raise InvoiceLockedError("Faktura została już wystawiona. Zmiany wprowadź przez korektę.")
    normalized = [normalize_position(p) for p in pozycje]
    if not normalized:
        raise ValueError("Faktura musi zawierać co najmniej jedną pozycję.")
    netto, brutto = totals(normalized)
    conn = get_conn()
    try:
        conn.execute(
            """
            UPDATE faktury
            SET pozycje_json = ?, kwota_netto = ?, kwota_brutto = ?, numer = ?,
                data_wystawienia = ?, data_sprzedazy = ?, termin_platnosci = ?,
                przyczyna_korekty = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                json.dumps(normalized, ensure_ascii=False), netto, brutto, (numer or "").strip() or None,
                data_wystawienia, data_sprzedazy, termin_platnosci,
                przyczyna_korekty if invoice.get("rodzaj") == "korekta" else invoice.get("przyczyna_korekty"),
                invoice_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return netto, brutto


def delete_draft_invoice(invoice_id: int) -> None:
    invoice = get_invoice_by_id(invoice_id)
    if not invoice:
        return
    if invoice.get("eksportowana"):
        raise InvoiceLockedError("Wystawionej faktury nie można usunąć. Utwórz korektę.")
    conn = get_conn()
    try:
        conn.execute("DELETE FROM faktury WHERE id = ?", (invoice_id,))
        conn.commit()
    finally:
        conn.close()


def create_correction(invoice_id: int, przyczyna: str = "") -> int:
    """Tworzy roboczą korektę wystawionej faktury (pozycje 'do zera', do edycji)."""
    original = get_invoice_by_id(invoice_id)
    if not original:
        raise ValueError(f"Nie znaleziono faktury o id {invoice_id}.")
    if not original.get("eksportowana"):
        raise ValueError("Korektę można wystawić tylko do wystawionej faktury. Roboczą fakturę po prostu edytuj.")
    positions = reverse_positions(get_invoice_positions(invoice_id))
    netto, brutto = totals(positions)
    today = _now_date()
    columns = [
        "nabywca_skrocona", "nabywca_pelna", "nabywca_adres", "nabywca_kod", "nabywca_miasto",
        "nabywca_nip", "nabywca_regon", "nabywca_kraj", "nabywca_email", "wystawca",
        "konto_bankowe", "gtu", "metoda_kasowa", "wyslij_do_ksef", "wyslij_do_kontrahenta",
    ]
    conn = get_conn()
    try:
        cur = conn.execute(
            f"""
            INSERT INTO faktury (
                firma_id, miesiac, kwota_netto, kwota_brutto, data_wystawienia, data_sprzedazy,
                termin_platnosci, pozycje_json, eksportowana, status, rodzaj,
                korygowana_faktura_id, przyczyna_korekty, {', '.join(columns)}
            )
            SELECT firma_id, miesiac, ?, ?, ?, ?, ?, ?, 0, 'robocza', 'korekta', id, ?,
                   {', '.join(columns)}
            FROM faktury WHERE id = ?
            """,
            (netto, brutto, today, today, original.get("termin_platnosci") or today,
             json.dumps(positions, ensure_ascii=False), przyczyna or "", invoice_id),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def set_ksef_number(invoice_id: int, ksef_numer: str) -> None:
    ksef_numer = (ksef_numer or "").strip()
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE faktury SET ksef_numer = ?, ksef_data = CASE WHEN ? = '' THEN NULL ELSE CURRENT_TIMESTAMP END, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (ksef_numer or None, ksef_numer, invoice_id),
        )
        conn.commit()
    finally:
        conn.close()

def list_issued_without_ksef() -> List[Dict[str, Any]]:
    """Wystawione faktury bez numeru KSeF, z NIP-em firmy, do dopasowania po firmie i kwocie."""
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT f.id, f.numer, f.miesiac, f.kwota_brutto, f.firma_id, fi.nip AS firma_nip "
            "FROM faktury f JOIN firmy fi ON fi.id = f.firma_id "
            "WHERE COALESCE(f.eksportowana, 0) = 1 AND COALESCE(f.ksef_numer, '') = '' "
            "ORDER BY f.miesiac, f.id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_invoice_number(invoice_id: int, numer: str) -> None:
    conn = get_conn()
    try:
        conn.execute("UPDATE faktury SET numer = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (numer, invoice_id))
        conn.commit()
    finally:
        conn.close()