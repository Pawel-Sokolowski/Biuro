from decimal import Decimal, InvalidOperation
import re
from typing import Optional

from app.db.dao_conn import get_conn
from app.db.dao_tasks import BILLED_ACTIVITY_TASK_ERROR, _allowed_task_ids, create_task_in_transaction
from app.security.authorization import assigned_company_ids, is_administrator


def _check_company_access(conn, actor_id: int, firma_id: int) -> None:
    if not actor_id or not conn.execute(
        "SELECT 1 FROM pracownicy WHERE id = %s AND aktywny = 1", (actor_id,)
    ).fetchone():
        raise PermissionError("Wymagany jest aktywny użytkownik.")
    if not is_administrator(actor_id) and firma_id not in assigned_company_ids(actor_id):
        raise PermissionError("Brak dostępu do tej firmy.")
    if not conn.execute("SELECT id FROM firmy WHERE id = %s FOR UPDATE", (firma_id,)).fetchone():
        raise ValueError("Nie znaleziono firmy.")


def _check_task(conn, task_id: int, firma_id: int, actor_id: int):
    task = conn.execute("SELECT * FROM zadania WHERE id = %s FOR UPDATE", (task_id,)).fetchone()
    if task is None:
        raise ValueError("Nie znaleziono zadania.")
    if task["firma_id"] != firma_id:
        raise ValueError("Zadanie i czynność dodatkowa muszą dotyczyć tej samej firmy.")
    if task_id not in _allowed_task_ids(conn, [task_id], actor_id):
        raise PermissionError("Brak dostępu do tego zadania.")
    return task


def _validate_activity(miesiac, ilosc, cena_netto):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(miesiac)):
        raise ValueError("Podaj miesiąc w formacie RRRR-MM.")
    try:
        quantity, price = Decimal(str(ilosc)), Decimal(str(cena_netto))
        if not quantity.is_finite() or quantity <= 0 or not price.is_finite() or price < 0:
            raise ValueError("Ilość musi być dodatnia, a cena nieujemna.")
    except InvalidOperation as exc:
        raise ValueError("Nieprawidłowa ilość lub cena czynności.") from exc


def _check_billed_activity_status(conn, service_id, status):
    if status != "Zakończone" and conn.execute(
        "SELECT 1 FROM faktury_uslugi_dodatkowe a JOIN faktury i ON i.id = a.invoice_id "
        "WHERE a.extra_service_row_id = %s AND COALESCE(i.eksportowana, 0) = 1 LIMIT 1", (service_id,)
    ).fetchone():
        raise ValueError(BILLED_ACTIVITY_TASK_ERROR)


def _get_activity_for_update(conn, service_id, actor_id):
    activity = conn.execute(
        "SELECT uf.*, u.nazwa FROM uslugi_dodatkowe_firma uf "
        "JOIN uslugi_dodatkowe u ON u.id = uf.usluga_id WHERE uf.id = %s", (service_id,)
    ).fetchone()
    if activity is None:
        raise ValueError("Nie znaleziono czynności dodatkowej.")
    _check_company_access(conn, actor_id, activity["firma_id"])
    # Ten sam porządek blokad co generowanie faktur: firma, czynność, zadanie.
    conn.execute("SELECT id FROM firmy WHERE id = %s FOR UPDATE", (activity["firma_id"],)).fetchone()
    from app.db.dao_invoices import _backfill_extra_service_allocations
    _backfill_extra_service_allocations(conn, activity["firma_id"], activity["miesiac"])
    return conn.execute(
        "SELECT uf.*, u.nazwa FROM uslugi_dodatkowe_firma uf "
        "JOIN uslugi_dodatkowe u ON u.id = uf.usluga_id WHERE uf.id = %s FOR UPDATE OF uf", (service_id,)
    ).fetchone()


def _insert_activity(conn, firma_id, miesiac, usluga_id, ilosc, cena_netto, fakturowac, jednostka, zadanie_id):
    return int(conn.execute(
        "INSERT INTO uslugi_dodatkowe_firma "
        "(firma_id, miesiac, usluga_id, ilosc, cena_netto, fakturowac, jednostka, zadanie_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (firma_id, miesiac, usluga_id, ilosc, cena_netto, 1 if fakturowac else 0, jednostka, zadanie_id),
    ).fetchone()[0])


def add_company_extra_service(
    firma_id: int,
    miesiac: str,
    usluga_id: int,
    ilosc: float,
    cena_netto: float,
    fakturowac: bool,
    jednostka: str = "szt.",
    zadanie_id: Optional[int] = None,
    actor_id: Optional[int] = None,
) -> int:
    conn = get_conn()
    try:
        if actor_id is not None:
            _check_company_access(conn, actor_id, firma_id)
            _validate_activity(miesiac, ilosc, cena_netto)
        if zadanie_id is not None:
            if actor_id is None:
                raise PermissionError("Podaj użytkownika przypisującego zadanie do czynności.")
            _check_task(conn, zadanie_id, firma_id, actor_id)
        new_id = _insert_activity(conn, firma_id, miesiac, usluga_id, ilosc, cena_netto, fakturowac, jednostka, zadanie_id)
        conn.commit()
        return new_id
    finally:
        conn.close()


def create_extra_activity(
    firma_id: int, miesiac: str, usluga_id: int, ilosc: float, cena_netto: float,
    fakturowac: bool, jednostka: str = "szt.", *, actor_id: int,
    task_id: Optional[int] = None, task_title: Optional[str] = None,
    task_description: str = "", task_employee_id: Optional[int] = None,
    task_due: Optional[str] = None, task_status: str = "Nowe",
) -> int:
    """Atomowo dodaje planowaną czynność i nowe lub istniejące zadanie."""
    _validate_activity(miesiac, ilosc, cena_netto)
    if (task_id is None) == (not bool(task_title and task_title.strip())):
        raise ValueError("Wybierz istniejące zadanie albo podaj tytuł nowego.")
    conn = get_conn()
    try:
        _check_company_access(conn, actor_id, firma_id)
        if task_id is None:
            task_id = create_task_in_transaction(
                conn, task_title, task_description, task_status, task_due,
                firma_id, task_employee_id, actor_id, actor_id,
            )
        else:
            _check_task(conn, task_id, firma_id, actor_id)
        result = _insert_activity(conn, firma_id, miesiac, usluga_id, ilosc, cena_netto, fakturowac, jednostka, task_id)
        conn.commit()
        return result
    finally:
        conn.close()


def link_extra_activity_task(service_row_id: int, task_id: int, actor_id: int) -> None:
    conn = get_conn()
    try:
        activity = _get_activity_for_update(conn, service_row_id, actor_id)
        if activity["zadanie_id"] is not None:
            _check_task(conn, activity["zadanie_id"], activity["firma_id"], actor_id)
        task = _check_task(conn, task_id, activity["firma_id"], actor_id)
        _check_billed_activity_status(conn, service_row_id, task["status"])
        conn.execute("UPDATE uslugi_dodatkowe_firma SET zadanie_id = %s WHERE id = %s", (task_id, service_row_id))
        conn.commit()
    finally:
        conn.close()


def create_task_for_extra_activity(
    service_id: int, actor_id: int, task_title: Optional[str] = None,
    pracownik_id: Optional[int] = None, status: str = "Nowe", termin: Optional[str] = None, opis: str = "",
) -> int:
    """Dopisuje zadanie do historycznej czynności; bez osieroconego zadania przy błędzie."""
    conn = get_conn()
    try:
        activity = _get_activity_for_update(conn, service_id, actor_id)
        if activity["zadanie_id"] is not None:
            raise ValueError("Czynność ma już przypisane zadanie.")
        _check_billed_activity_status(conn, service_id, status)
        task_id = create_task_in_transaction(
            conn, task_title or activity["nazwa"], opis, status, termin,
            activity["firma_id"], pracownik_id, actor_id, actor_id,
        )
        conn.execute("UPDATE uslugi_dodatkowe_firma SET zadanie_id = %s WHERE id = %s", (task_id, service_id))
        conn.commit()
        return task_id
    finally:
        conn.close()


def list_extra_activities(
    viewer_id: int, firma_id: Optional[int] = None, miesiac: Optional[str] = None, state: Optional[str] = None,
) -> list[dict]:
    """Status czynności pochodzi zawsze z zadania, nigdy z kopii jego flagi."""
    if state not in (None, "pending", "done", "cancelled", "unlinked"):
        raise ValueError("Nieznany stan czynności.")
    conn = get_conn()
    try:
        if not viewer_id or not conn.execute(
            "SELECT 1 FROM pracownicy WHERE id = %s AND aktywny = 1", (viewer_id,)
        ).fetchone():
            raise PermissionError("Wymagany jest aktywny użytkownik.")
        filters, params = [], []
        for column, value in (("uf.firma_id", firma_id), ("uf.miesiac", miesiac)):
            if value is not None:
                filters.append(f"{column} = %s")
                params.append(value)
        if not is_administrator(viewer_id):
            filters.append("uf.firma_id = ANY(%s)")
            params.append(sorted(assigned_company_ids(viewer_id)))
            filters.append("(uf.zadanie_id IS NULL OR z.pracownik_id = %s OR z.created_by = %s "
                           "OR EXISTS (SELECT 1 FROM pracownik_firma pf WHERE pf.pracownik_id = %s AND pf.firma_id = uf.firma_id))")
            params.extend([viewer_id, viewer_id, viewer_id])
        where = "WHERE " + " AND ".join(filters) if filters else ""
        rows = conn.execute(f"""
            SELECT uf.*, f.nazwa AS firma_nazwa, u.nazwa, u.stawka_vat,
                   z.tytul AS task_title, z.status AS task_status,
                   z.pracownik_id AS task_employee_id, z.termin AS task_due,
                   p.imie || ' ' || p.nazwisko AS task_employee_name,
                   CASE WHEN uf.zadanie_id IS NULL THEN 'unlinked'
                        WHEN z.status = 'Zakończone' THEN 'done'
                        WHEN z.status = 'Anulowane' THEN 'cancelled'
                        ELSE 'pending' END AS state
            FROM uslugi_dodatkowe_firma uf
            JOIN firmy f ON f.id = uf.firma_id
            JOIN uslugi_dodatkowe u ON u.id = uf.usluga_id
            LEFT JOIN zadania z ON z.id = uf.zadanie_id
            LEFT JOIN pracownicy p ON p.id = z.pracownik_id
            {where} ORDER BY uf.miesiac DESC, f.nazwa, uf.id DESC
        """, params).fetchall()
        return [dict(row) for row in rows if state is None or row["state"] == state]
    finally:
        conn.close()
