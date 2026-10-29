"""Uzgadnianie wyciągów ING MT940 z fakturami: zatwierdzanie zawsze ręczne."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import mt940

from app.db.dao_conn import get_conn
from app.db.dao_invoices import can_access_invoice
from app.security.authorization import can, is_administrator


class PaymentError(ValueError):
    pass


def _allowed(actor_id: int) -> bool:
    return bool(actor_id and (is_administrator(actor_id) or can(actor_id, "can_view_finances")))


def _money(text: str) -> Decimal:
    try:
        amount = Decimal(str(text).replace(" ", "").replace(",", "."))
        if not amount.is_finite() or amount != amount.quantize(Decimal("0.01")):
            raise PaymentError("Kwota musi być skończoną liczbą z najwyżej dwoma miejscami po przecinku.")
        return amount.quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError) as exc:
        raise PaymentError(f"Nieprawidłowa kwota przelewu: {text}") from exc


def parse_ing_mt940(raw: bytes) -> list[dict[str, Any]]:
    """Parser biblioteki mt-940; import zachowuje walutę i rodzaj operacji.

    Każdy komunikat :20: parsujemy osobno, aby metadane kolejnego wyciągu
    nie zmieniały identyfikatora poprzednich operacji. Zapłaty wykorzystują
    datę księgowania (jeśli jest obecna); data waluty pozostaje w ewidencji.
    """
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            source = raw.decode("cp1250")
        except UnicodeDecodeError as exc:
            raise PaymentError("Nie można odczytać kodowania wyciągu MT940.") from exc
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    result: list[dict[str, Any]] = []
    for statement_source in re.split(r"(?=^:20:)", source, flags=re.MULTILINE):
        if not re.search(r"^:61:", statement_source, flags=re.MULTILINE):
            continue
        try:
            statement = mt940.parse(statement_source)
            expected_count = len(re.findall(r"^:61:", statement_source, flags=re.MULTILINE))
            if len(statement) != expected_count:
                raise PaymentError("Nie wszystkie operacje :61: zostały poprawnie odczytane.")
            account = re.sub(r"\s+", "", str(statement.data.get("account_identification") or ""))
            statement_id = str(statement.data.get("statement_number") or "")
            sequence = str(statement.data.get("sequence_number") or "")
            if not account or not statement_id:
                raise PaymentError("Wyciąg wymaga rachunku :25: i numeru :28C:.")
            for ordinal, transaction in enumerate(statement, 1):
                data = transaction.data
                currency = str(data.get("currency") or "").upper()
                if not re.fullmatch(r"[A-Z]{3}", currency):
                    raise PaymentError("Wyciąg nie określa waluty. Wymagane jest saldo :60F: lub :60M:.")
                direction = data["status"]
                amount = abs(_money(str(data["amount"].amount)))
                if direction in ("D", "RC"):
                    amount = -amount
                value_date = data["date"].isoformat()
                booked_date = (data.get("entry_date") or data["date"]).isoformat()
                title = " ".join(str(data.get("transaction_details") or "").split())
                bank_reference = str(data.get("bank_reference") or "").strip()
                reference = bank_reference or str(data.get("customer_reference") or "")
                if bank_reference and bank_reference.upper().replace(" ", "") not in {"NONREF", "NOTPROVIDED", "N/A", "UNKNOWN"}:
                    # Referencja banku identyfikuje operację także w eksportach
                    # o innych numerach wyciągu i kolejności wierszy.
                    identity = [account, bank_reference, value_date, booked_date, str(amount), currency, direction]
                else:
                    # Bez unikalnej referencji banku deduplikacja dotyczy tego
                    # samego wyciągu; identyczne rzeczywiste wpłaty zachowują osobne pozycje.
                    identity = [account, statement_id, sequence, ordinal,
                                str(data.get("transaction_reference") or ""), value_date, booked_date,
                                str(amount), currency, direction, reference, title]
                digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()
                result.append({
                    "ordinal": ordinal, "date": booked_date, "value_date": value_date,
                    "amount": amount, "currency": currency, "direction": direction,
                    "account": account, "reference": reference, "title": title, "hash": digest,
                })
        except PaymentError:
            raise
        except (ValueError, KeyError, TypeError, InvalidOperation, RuntimeError) as exc:
            raise PaymentError(f"Niepoprawny wyciąg MT940: {exc}") from exc
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
                  (hash, rachunek, data_operacji, data_waluty, kwota, waluta, kierunek, kontrahent, tytul, referencja)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(hash) DO NOTHING RETURNING id
                """,
                (entry["hash"], entry["account"], entry["date"], entry["value_date"], entry["amount"],
                 entry["currency"], entry["direction"], "", entry["title"], entry["reference"]),
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
            WHERE b.kwota > 0 AND b.waluta = 'PLN' AND b.kierunek = 'C'
              AND b.kwota - COALESCE(p.used, 0) > 0.009
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
                   CAST(i.kwota_brutto AS NUMERIC(14,2)) - COALESCE(p.paid, 0) AS balance
            FROM faktury i
            LEFT JOIN firmy f ON f.id = i.firma_id
            LEFT JOIN (
                SELECT faktura_id, SUM(kwota) AS paid FROM faktury_platnosci GROUP BY faktura_id
            ) p ON p.faktura_id = i.id
            WHERE i.rodzaj = 'faktura' AND COALESCE(i.eksportowana, 0) = 1
              AND CAST(i.kwota_brutto AS NUMERIC(14,2)) > COALESCE(p.paid, 0)
            ORDER BY i.data_wystawienia DESC NULLS LAST, i.id DESC LIMIT 2000
            """
        ).fetchall()
        return [dict(row) for row in rows if can_access_invoice(actor_id, row["firma_id"])]
    finally:
        conn.close()


def suggested_invoice_ids(transaction: dict[str, Any], invoices: list[dict[str, Any]]) -> list[int]:
    """Propozycje wg numeru faktury, NIP i kwoty, zawsze do potwierdzenia.

    Kwota, numer czy NIP same w sobie nie stanowią dowodu zapłaty.
    """
    title = re.sub(r"\s+", "", str(transaction.get("tytul") or "")).casefold()
    numeric_title = re.sub(r"\D", "", title)
    amount = _money(str(transaction.get("remaining") or transaction.get("kwota") or 0))
    results = []
    for inv in invoices:
        number = re.sub(r"\s+", "", str(inv.get("numer") or "")).casefold()
        nip = re.sub(r"\D", "", str(inv.get("nip") or ""))
        balance = _money(str(inv["balance"]))
        invoice_in_title = bool(number and len(number) >= 5 and number in title)
        nip_in_title = bool(len(nip) == 10 and nip in numeric_title)
        exact_amount = balance == amount
        if invoice_in_title and nip_in_title and exact_amount:
            score = 0
        elif invoice_in_title and exact_amount:
            score = 1
        elif invoice_in_title:
            score = 2
        elif nip_in_title and exact_amount:
            score = 3
        else:
            continue
        results.append((score, int(inv["id"])))
    results.sort()
    return [invoice_id for _, invoice_id in results]


def _check_date(value: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError) as exc:
        raise PaymentError("Podaj datę w formacie RRRR-MM-DD.") from exc


def get_invoice_payment_summary(invoice_id: int, actor_id: int) -> dict[str, Any]:
    if not _allowed(actor_id):
        raise PermissionError("Brak uprawnień do przeglądania rozliczeń.")
    conn = get_conn()
    try:
        invoice = conn.execute("SELECT * FROM faktury WHERE id = %s", (invoice_id,)).fetchone()
        if not invoice or not can_access_invoice(actor_id, invoice["firma_id"]):
            raise PaymentError("Nie znaleziono dostępnej faktury.")
        if invoice["rodzaj"] != "faktura" or not invoice["eksportowana"]:
            raise PaymentError("Rozliczać można tylko wystawione faktury.")
        payments = [dict(row) for row in conn.execute(
            "SELECT * FROM faktury_platnosci WHERE faktura_id = %s ORDER BY data_zaplaty, id",
            (invoice_id,),
        ).fetchall()]
        total = _money(str(invoice["kwota_brutto"]))
        paid = sum((row["kwota"] for row in payments), Decimal("0.00"))
        last_date = max((row["data_zaplaty"] for row in payments), default=None)
        return {"total": total, "paid": paid, "balance": total - paid, "payments": payments,
                "paid_on": last_date.isoformat() if last_date and total > 0 and paid >= total else None}
    finally:
        conn.close()


def monthly_invoice_payments(month: str) -> dict[int, list[dict[str, Any]]]:
    """Wszystkie faktury wystawione za miesiąc, wraz z historią wpłat."""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT i.id, i.firma_id, i.numer, i.kwota_brutto,
                   p.id AS payment_id, p.kwota, p.data_zaplaty, p.sposob
            FROM faktury i LEFT JOIN faktury_platnosci p ON p.faktura_id = i.id
            WHERE i.miesiac = %s AND i.rodzaj = 'faktura'
              AND COALESCE(i.eksportowana, 0) = 1 AND i.firma_id IS NOT NULL
            ORDER BY i.id, p.data_zaplaty, p.id
        """, (month,)).fetchall()
        invoices = {}
        for row in rows:
            invoice = invoices.setdefault(row["id"], {
                "id": row["id"], "firma_id": row["firma_id"], "numer": row["numer"] or "—",
                "total": _money(str(row["kwota_brutto"])), "payments": [],
            })
            if row["payment_id"] is not None:
                invoice["payments"].append({"kwota": row["kwota"], "data_zaplaty": row["data_zaplaty"], "sposob": row["sposob"]})
        companies = {}
        for invoice in invoices.values():
            paid = sum((p["kwota"] for p in invoice["payments"]), Decimal("0.00"))
            last_date = max((p["data_zaplaty"] for p in invoice["payments"]), default=None)
            invoice["paid"] = paid
            invoice["balance"] = invoice["total"] - paid
            invoice["paid_on"] = last_date if last_date and invoice["total"] > 0 and paid >= invoice["total"] else None
            companies.setdefault(invoice["firma_id"], []).append(invoice)
        return companies
    finally:
        conn.close()


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
        if transfer["waluta"] != "PLN":
            raise PaymentError("Do faktury w PLN można przypisać wyłącznie przelew w PLN.")
        if transfer["kierunek"] != "C":
            raise PaymentError("Rozliczać można tylko zwykły wpływ, bez storna i zwrotów.")
        if transfer["kwota"] <= 0 or invoice["rodzaj"] != "faktura" or not invoice["eksportowana"]:
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
        if not inv["eksportowana"]:
            raise PaymentError("Rozliczać można tylko wystawione faktury.")
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
