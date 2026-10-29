"""Uzgadnianie wyciągów ING MT940 z fakturami: zatwierdzanie zawsze ręczne."""
from __future__ import annotations

import hashlib
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.db.dao_conn import get_conn
from app.db.dao_invoices import can_access_invoice
from app.security.authorization import can, is_administrator


class PaymentError(ValueError):
    pass


def _allowed(actor_id: int) -> bool:
    return bool(actor_id and (is_administrator(actor_id) or can(actor_id, "can_view_finances")))


def _money(text: str) -> Decimal:
    try:
        return Decimal(str(text).replace(" ", "").replace(",", ".")).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise PaymentError(f"Nieprawidłowa kwota przelewu: {text}") from exc


def parse_ing_mt940(raw: bytes) -> list[dict[str, Any]]:
    """SWIFT MT940: :25:, :28C:, :61:, :86: i wieloliniowe opisy ING.

    Parsowanie nie oznacza rozliczenia. Operacje D są importowane, ale nie
    można ich wykorzystać jako dodatniej zapłaty za fakturę sprzedaży.
    """
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        source = raw.decode("cp1250")
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    # MT940 bywa umieszczany w blokach SWIFT {4: ... -}.
    lines = source.splitlines()
    result: list[dict[str, Any]] = []
    account = ""
    statement = ""
    current = None
    active_tag = ""

    def flush():
        nonlocal current
        if current is not None:
            current["title"] = " ".join(current.pop("description", [])).strip()
            raw_id = "|".join((
                account, statement, str(current["ordinal"]), current["date"],
                str(current["amount"]), current["currency"], current["reference"],
                current["title"],
            ))
            current["hash"] = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()
            result.append(current)
            current = None

    for line in lines:
        tag = re.match(r"^:(20|25|28C|61|86):?(.*)$", line)
        if tag:
            code, text = tag.groups()
            active_tag = code
            if code == "25":
                account = text.strip()
            elif code == "28C":
                statement = text.strip()
            elif code == "61":
                flush()
                # YYMMDD value date, optional MMDD booking date, C/D or reversal
                # and then amount with comma as decimal separator.
                m = re.match(r"^(\d{6})(?:\d{4})?([RC]{0,1}[CD])([A-Z])?(\d+(?:,\d{0,2})?)", text.strip())
                if not m:
                    raise PaymentError(f"Niepoprawny rekord :61: w wyciągu: {text[:90]}")
                raw_date, direction, _, raw_amount = m.groups()
                yy = int(raw_date[:2])
                year = 2000 + yy if yy <= 79 else 1900 + yy
                try:
                    day = date(year, int(raw_date[2:4]), int(raw_date[4:6])).isoformat()
                except ValueError as exc:
                    raise PaymentError(f"Niepoprawna data operacji MT940: {raw_date}") from exc
                amount = _money(raw_amount)
                if direction in ("D", "RC"):
                    amount = -amount
                current = {
                    "ordinal": len(result) + 1, "date": day,
                    "amount": amount, "currency": "PLN",
                    "account": account, "reference": text[m.end():].strip(),
                    "description": [],
                }
            elif code == "86" and current is not None:
                current["description"].append(text.strip())
        elif current is not None and active_tag == "86" and line.strip() and line.strip() not in ("-", "}"):
            current["description"].append(line.strip())
    flush()
    if not result:
        raise PaymentError("Plik nie zawiera transakcji MT940 (:61:).")
    return result


def import_mt940_file(path: str, actor_id: int) -> dict[str, int]:
    if not _allowed(actor_id):
        raise PermissionError("Brak uprawnień do importu operacji bankowych.")
    source = Path(path)
    if source.stat().st_size > 20 * 1024 * 1024:
        raise PaymentError("Wyciąg przekracza 20 MB.")
    records = parse_ing_mt940(source.read_bytes())
    conn = get_conn()
    inserted = 0
    try:
        for entry in records:
            cur = conn.execute(
                """
                INSERT INTO bank_transakcje
                  (hash, rachunek, data_operacji, kwota, waluta, kontrahent, tytul, referencja)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(hash) DO NOTHING RETURNING id
                """,
                (entry["hash"], entry["account"], entry["date"], entry["amount"],
                 entry["currency"], "", entry["title"], entry["reference"]),
            )
            if cur.fetchone():
                inserted += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"all": len(records), "new": inserted, "duplicates": len(records) - inserted}


def list_unsettled_transfers(actor_id: int) -> list[dict[str, Any]]:
    if not _allowed(actor_id):
        raise PermissionError("Brak uprawnień do przeglądania wyciągów.")
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT b.*, b.kwota - COALESCE(p.used, 0) AS remaining
            FROM bank_transakcje b
            LEFT JOIN (
                SELECT transakcja_id, SUM(kwota) AS used FROM faktury_platnosci
                WHERE transakcja_id IS NOT NULL GROUP BY transakcja_id
            ) p ON p.transakcja_id = b.id
            WHERE b.kwota > 0 AND b.kwota - COALESCE(p.used, 0) > 0.009
            ORDER BY b.data_operacji DESC, b.id DESC
            LIMIT 1000
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_invoice_choices(actor_id: int) -> list[dict[str, Any]]:
    if not _allowed(actor_id):
        raise PermissionError("Brak uprawnień do rozliczania faktur.")
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT i.id, i.firma_id, i.numer, i.miesiac, i.kwota_brutto,
                   COALESCE(NULLIF(f.nazwa_skrocona, ''), f.nazwa, i.nabywca_pelna, '') AS buyer,
                   COALESCE(f.nip, i.nabywca_nip, '') AS nip,
                   i.kwota_brutto - COALESCE(p.paid, 0) AS balance
            FROM faktury i
            LEFT JOIN firmy f ON f.id = i.firma_id
            LEFT JOIN (
                SELECT faktura_id, SUM(kwota) AS paid FROM faktury_platnosci GROUP BY faktura_id
            ) p ON p.faktura_id = i.id
            WHERE i.rodzaj = 'faktura' AND COALESCE(i.eksportowana, 0) = 1
              AND i.kwota_brutto > COALESCE(p.paid, 0) + 0.009
            ORDER BY i.data_wystawienia DESC NULLS LAST, i.id DESC LIMIT 2000
            """
        ).fetchall()
        return [dict(row) for row in rows if can_access_invoice(actor_id, row["firma_id"])]
    finally:
        conn.close()


def suggested_invoice_ids(transaction: dict[str, Any], invoices: list[dict[str, Any]]) -> list[int]:
    """Sugeruje, lecz nigdy nie rozlicza bez potwierdzenia użytkownika."""
    title = re.sub(r"\s+", "", str(transaction.get("tytul") or "")).casefold()
    amount = _money(str(transaction.get("remaining") or transaction.get("kwota") or 0))
    results = []
    for inv in invoices:
        number = re.sub(r"\s+", "", str(inv.get("numer") or "")).casefold()
        if not number or number not in title:
            continue
        balance = _money(str(inv["balance"]))
        # Preferuj dokładne dopasowanie kwot, dopuszczając częściową zapłatę.
        results.append((0 if balance == amount else 1, int(inv["id"])))
    results.sort()
    return [id for _, id in results]


def _check_date(value: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError) as exc:
        raise PaymentError("Podaj datę w formacie RRRR-MM-DD.") from exc


def confirm_bank_payment(transaction_id: int, invoice_id: int, amount: Any, actor_id: int) -> None:
    if not _allowed(actor_id):
        raise PermissionError("Brak uprawnień do rozliczania faktur.")
    amount = _money(str(amount))
    if amount <= 0:
        raise PaymentError("Kwota rozliczenia musi być dodatnia.")
    conn = get_conn()
    try:
        transfer = conn.execute("SELECT * FROM bank_transakcje WHERE id = %s FOR UPDATE", (transaction_id,)).fetchone()
        invoice = conn.execute("SELECT * FROM faktury WHERE id = %s FOR UPDATE", (invoice_id,)).fetchone()
        if not transfer or not invoice:
            raise PaymentError("Nie znaleziono operacji bankowej lub faktury.")
        if not can_access_invoice(actor_id, invoice["firma_id"]):
            raise PermissionError("Brak dostępu do wskazanej faktury.")
        if transfer["kwota"] <= 0 or invoice["rodzaj"] != "faktura":
            raise PaymentError("Można rozliczyć tylko wpływ na właściwą fakturę.")
        already_transfer = conn.execute(
            "SELECT COALESCE(SUM(kwota), 0) FROM faktury_platnosci WHERE transakcja_id = %s",
            (transaction_id,),
        ).fetchone()[0]
        already_invoice = conn.execute(
            "SELECT COALESCE(SUM(kwota), 0) FROM faktury_platnosci WHERE faktura_id = %s",
            (invoice_id,),
        ).fetchone()[0]
        if amount > transfer["kwota"] - already_transfer + Decimal("0.009"):
            raise PaymentError("Kwota przekracza niewykorzystaną wartość przelewu.")
        if amount > Decimal(str(invoice["kwota_brutto"])) - already_invoice + Decimal("0.009"):
            raise PaymentError("Kwota przekracza nierozliczone saldo faktury.")
        conn.execute(
            """
            INSERT INTO faktury_platnosci
              (faktura_id, transakcja_id, data_zaplaty, kwota, sposob, pracownik_id)
            VALUES (%s, %s, %s, %s, 'bank', %s)
            """,
            (invoice_id, transaction_id, transfer["data_operacji"], amount, actor_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_cash_payment(invoice_id: int, paid_on: str, amount: Any, actor_id: int) -> None:
    if not _allowed(actor_id):
        raise PermissionError("Brak uprawnień do oznaczenia płatności gotówką.")
    when = _check_date(paid_on)
    payment = _money(str(amount))
    if payment <= 0:
        raise PaymentError("Kwota musi być dodatnia.")
    conn = get_conn()
    try:
        inv = conn.execute("SELECT * FROM faktury WHERE id = %s FOR UPDATE", (invoice_id,)).fetchone()
        if not inv or inv["rodzaj"] != "faktura" or not can_access_invoice(actor_id, inv["firma_id"]):
            raise PaymentError("Nie znaleziono dostępnej faktury.")
        paid = conn.execute(
            "SELECT COALESCE(SUM(kwota), 0) FROM faktury_platnosci WHERE faktura_id = %s",
            (invoice_id,),
        ).fetchone()[0]
        if payment > Decimal(str(inv["kwota_brutto"])) - paid + Decimal("0.009"):
            raise PaymentError("Kwota przekracza pozostałe saldo faktury.")
        conn.execute(
            """
            INSERT INTO faktury_platnosci
              (faktura_id, data_zaplaty, kwota, sposob, pracownik_id)
            VALUES (%s, %s, %s, 'gotowka', %s)
            """, (invoice_id, when, payment, actor_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
