from __future__ import annotations

import datetime
from calendar import monthrange
import psycopg
from datetime import date
from typing import Any, Dict, List, Optional

from app.db.dao_conn import get_conn
from app.utils.months import month_bounds
from app.db.dao_monthly import get_month_status, save_month_status
from app.db.dao_invoices import add_invoice_item_for_service
from app.db.dao_companies import get_company, save_company
from app.db.dao_notes import get_latest_notes_for_firms
from app.utils.timer import TimerUtils
from app.utils.months import default_month_for_monthly_view
from app.security.authorization import assigned_company_ids, can, is_administrator
from app.db import dao_active_timers


class MonthlyViewLogic:
    _TIMER_FIELDS = {
        "ksiegowosc": (
            "aktywna_firma_stoper_ks",
            "czas_start_ks",
            "aktywna_firma_stoper_ks_activity",
            "sekundy_ksiegowosc",
        ),
        "kadry": (
            "aktywna_firma_stoper_kadry",
            "czas_start_kadry",
            "aktywna_firma_stoper_kadry_activity",
            "sekundy_kadry",
        ),
    }

    def __init__(self, pracownik_id: int):
        self.pracownik_id = pracownik_id
        self.aktualny_miesiac = default_month_for_monthly_view()

        self.aktywna_firma_stoper_ks: Optional[int] = None
        self.aktywna_firma_stoper_kadry: Optional[int] = None
        self.czas_start_ks: Optional[datetime.datetime] = None
        self.czas_start_kadry: Optional[datetime.datetime] = None

        self.aktywna_firma_stoper_ks_activity: Optional[Dict[str, Any]] = None
        self.aktywna_firma_stoper_kadry_activity: Optional[Dict[str, Any]] = None

        self.czas_start_general: Optional[datetime.datetime] = None
        self.general_activity: Optional[Dict[str, Any]] = None
        self._timer_label_cache = {}
        self._durable_timers = {}

        self._auto_check_suspension()

    def is_general_timer_running(self) -> bool:
        return self.czas_start_general is not None

    def stop_company_timers(self) -> None:
        """Zapisuje i zatrzymuje stopery firm (księgowość i kadry)."""
        now = datetime.datetime.now()
        for typ in ("ksiegowosc", "kadry"):
            fields = self._TIMER_FIELDS[typ]
            firma_id = getattr(self, fields[0])
            if firma_id is None or getattr(self, fields[1]) is None:
                continue
            conn = get_conn()
            saved_state = {field: getattr(self, field) for field in fields[:3]}
            saved_session = self._durable_timers.get(typ)
            try:
                self._ensure_month_status(firma_id)
                cur = conn.cursor()
                self._finish_timer(typ, now, cur)
                conn.commit()
            except Exception:
                conn.rollback()
                for field, value in saved_state.items():
                    setattr(self, field, value)
                if saved_session:
                    self._durable_timers[typ] = saved_session
                raise
            finally:
                conn.close()

    def start_general_timer(self, activity: Optional[Dict[str, Any]] = None) -> None:
        """Uruchamia stoper ogólny (bez firmy); działające stopery firm i poprzedni ogólny są zapisywane."""
        self.stop_company_timers()
        if self.is_general_timer_running():
            self.stop_general_timer()
        activity = dict(activity or {})
        task_id = activity.get("task_id")
        if task_id:
            conn = get_conn()
            try:
                conn.execute(
                    "UPDATE zadania SET status = 'W toku', updated_at = to_char(now(), 'YYYY-MM-DD HH24:MI:SS') "
                    "WHERE id = %s AND status = 'Nowe'",
                    (task_id,),
                )
                conn.commit()
            finally:
                conn.close()
        started = datetime.datetime.now()
        with get_conn() as conn:
            session = dao_active_timers.start(conn, self.pracownik_id, None,
                started.strftime('%Y-%m'), 'ogolne', activity, started)
        self.general_activity = activity
        self.czas_start_general = started
        self._durable_timers['ogolne'] = session

    def stop_general_timer(self) -> int:
        if self.czas_start_general is None:
            return 0
        now = datetime.datetime.now()
        start = self.czas_start_general
        delta = self._timer_elapsed('ogolne', start, now)
        activity = self.general_activity or {}
        conn = get_conn()
        try:
            dao_active_timers.finish(conn, self._durable_timers.get('ogolne'))
            conn.execute(
                "INSERT INTO czasomierze (firma_id, pracownik_id, miesiac, typ, usluga_id, fakturowac, "
                "task_id, rozpoczecie, zakonczenie, sekundy, opis) "
                "VALUES (NULL, %s, %s, 'ogolne', NULL, 0, %s, %s, %s, %s, %s)",
                (self.pracownik_id, start.strftime("%Y-%m"), activity.get("task_id"),
                 start.isoformat(timespec="seconds"), now.isoformat(timespec="seconds"), delta,
                 activity.get("task_title") or None),
            )
            conn.commit()
        finally:
            conn.close()
        self.czas_start_general = None
        self.general_activity = None
        self._durable_timers.pop('ogolne', None)
        return delta

    def _timer_elapsed(self, kind, started, now):
        session = self._durable_timers.get(kind)
        if session is not None and session.started_at == started:
            return session.elapsed()
        return max(0, int((now - started).total_seconds()))

    def timer_checkpoints(self):
        return [timer.checkpoint() for timer in self._durable_timers.values()]

    def resume_timer(self, session_id):
        self.stop_all_timers()
        row, session = dao_active_timers.claim(self.pracownik_id, session_id)
        self.aktualny_miesiac = row['miesiac']
        kind = row['typ']
        if kind == 'ogolne':
            self.general_activity = row['activity']
            self.czas_start_general = session.started_at
        else:
            fields = self._TIMER_FIELDS[kind]
            setattr(self, fields[0], row['firma_id'])
            setattr(self, fields[1], session.started_at)
            setattr(self, fields[2], row['activity'])
        self._durable_timers[kind] = session
        return row['miesiac']

    def stop_all_timers(self) -> None:
        """Zapisuje wszystkie działające stopery (np. przy zamykaniu programu)."""
        errors = []
        now = datetime.datetime.now()
        for typ in ("ksiegowosc", "kadry"):
            fields = self._TIMER_FIELDS[typ]
            firma_id = getattr(self, fields[0])
            if firma_id is None or getattr(self, fields[1]) is None:
                continue
            conn = get_conn()
            saved_state = {field: getattr(self, field) for field in fields[:3]}
            saved_session = self._durable_timers.get(typ)
            try:
                self._ensure_month_status(firma_id)
                cur = conn.cursor()
                self._finish_timer(typ, now, cur)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                for field, value in saved_state.items():
                    setattr(self, field, value)
                if saved_session:
                    self._durable_timers[typ] = saved_session
                errors.append(exc)
            finally:
                conn.close()
        try:
            self.stop_general_timer()
        except Exception as exc:
            errors.append(exc)
        if errors:
            raise errors[0]

    def get_general_timer_detail(self) -> Optional[Dict[str, str]]:
        if self.czas_start_general is None:
            return None
        elapsed = self._timer_elapsed('ogolne', self.czas_start_general, datetime.datetime.now())
        return {
            "task": (self.general_activity or {}).get("task_title") or "Zadania ogólne",
            "company": "Ogólne",
            "department": "Ogólne",
            "elapsed": self.format_seconds(elapsed),
        }

    def set_month(self, new_month: str):
        self.aktualny_miesiac = new_month

    def get_employee_filter_list(self) -> List:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, imie || ' ' || nazwisko AS label
            FROM pracownicy
            WHERE aktywny = 1
            ORDER BY nazwisko, imie
        """
        )
        rows = cur.fetchall()
        conn.close()
        result = [(0, "Wszyscy")]
        result.extend([(r["id"], r["label"]) for r in rows])
        return result

    def get_monthly_rows(
        self, selected_month: str, selected_employee: Optional[int], selected_status: str
    ) -> List[Dict[str, Any]]:
        # No employee selection means the logged-in employee by default.
        # The explicit 0 value still represents "Wszyscy".
        if selected_employee is None:
            selected_employee = self.pracownik_id

        conn = get_conn()
        cur = conn.cursor()
        month_start, month_end = month_bounds(selected_month)

        sql = """
            SELECT f.id, COALESCE(NULLIF(f.nazwa_skrocona, ''), f.nazwa) AS nazwa,
                   f.status, f.uslugi_zawieszone, f.ksiegowa_id, f.kadrowa_id,
                   f.vat_status, f.vat_sprawdzono, f.vat_sprawdzil,
                   f.vat_ue_status, f.vat_ue_sprawdzono, f.vat_ue_sprawdzil,
                   COALESCE(sm.etap, 'Brak') AS etap,
                   COALESCE(sm.etap_kadry, 'Brak') AS etap_kadry,
                   COALESCE(sm.dokumenty_dostarczone, 0) AS dokumenty_dostarczone,
                   sm.data_dostarczenia AS data_dostarczenia,
                   CASE WHEN di.firma_id IS NOT NULL THEN COALESCE(sm.ilosc_dok, 0)
                        ELSE COALESCE(NULLIF(sm.ilosc_dok, 0), doc.ilosc_dok, 0) END AS ilosc_dok,
                   COALESCE(sm.sekundy_ksiegowosc, 0) AS sekundy_ksiegowosc,
                   COALESCE(sm.sekundy_kadry, 0) AS sekundy_kadry,
                   COALESCE(k.uop_ilosc, f.uop_ilosc, 0) AS uop,
                   COALESCE(k.uz_ilosc, f.uz_ilosc, 0) AS uz,
                   COALESCE(k.uod_ilosc, f.uod_ilosc, 0) AS uod
            FROM firmy f
            LEFT JOIN statusy_miesieczne sm
                ON sm.firma_id = f.id AND sm.miesiac = %s
            LEFT JOIN monthly_document_imports di ON di.firma_id=f.id AND di.miesiac=sm.miesiac
            LEFT JOIN LATERAL (
                SELECT uop_ilosc, uz_ilosc, uod_ilosc
                FROM kadry_umowy_miesieczne
                WHERE firma_id = f.id AND miesiac <= %s
                ORDER BY miesiac DESC LIMIT 1
            ) k ON TRUE
            LEFT JOIN (
                SELECT firma_id, COUNT(*) AS ilosc_dok
                FROM dokumenty
                WHERE data_wyst >= %s AND data_wyst < %s
                GROUP BY firma_id
            ) doc ON doc.firma_id = f.id
        """
        params: List[Any] = [selected_month, selected_month, month_start, month_end]
        cur.execute(sql, params)

        rows = [dict(r) for r in cur.fetchall()]
        from app.utils.polish_sort import polish_sort_key

        rows.sort(key=lambda row: polish_sort_key(row.get("nazwa") or ""))

        conn.close()

        result: List[Dict[str, Any]] = []

        firm_ids = [r["id"] for r in rows]
        notes_map: Dict[int, Dict[str, Any]] = {}
        if firm_ids:
            notes_map = get_latest_notes_for_firms(firm_ids)

        from app.db.dao_payments import monthly_invoice_payments
        invoices_by_company = monthly_invoice_payments(selected_month)
        from app.db.dao_monthly import list_active_substitutions
        substitutions = list_active_substitutions()

        for r in rows:
            invoices = invoices_by_company.get(r["id"], [])
            r["invoice_payments"] = invoices
            r["numer_faktury"] = "\n".join(inv["numer"] for inv in invoices)
            r["faktura_zaplacona"] = "\n".join(
                inv["paid_on"].strftime("%d-%m-%Y") if inv["paid_on"] else "" for inv in invoices
            )
            details = []
            for inv in invoices:
                details.append(f"{inv['numer']}: wpłacono {inv['paid']:.2f} PLN; saldo {inv['balance']:.2f} PLN")
                for p in inv["payments"]:
                    method = "gotówka" if p["sposob"] == "gotowka" else "przelew"
                    details.append(f"  {p['data_zaplaty']:%d-%m-%Y}: {p['kwota']:.2f} PLN ({method})")
            r["faktura_platnosci_tooltip"] = "\n".join(details)
            if selected_status != "Wszystkie":
                if (r["status"] or "Aktywne") != selected_status:
                    continue

            if selected_employee:
                subs = (
                    (substitutions.get((r["id"], "ksiegowosc")) or {}).get("zastepca_id"),
                    (substitutions.get((r["id"], "kadry")) or {}).get("zastepca_id"),
                )
                if selected_employee not in (r.get("ksiegowa_id"), r.get("kadrowa_id"), *subs):
                    continue
            for role, key in (("ksiegowosc", "zast_ks"), ("kadry", "zast_kadry")):
                sub = substitutions.get((r["id"], role))
                r[f"{key}_id"] = sub["zastepca_id"] if sub else None
                r[f"{key}_za"] = (sub.get("zastepowany_nazwa") or "") if sub else ""

            note = notes_map.get(r["id"])
            if note:
                t = note["tresc"] or ""
                autor = note.get("autor_nazwa") or ""
                data = note.get("data_utworzenia") or ""
            else:
                t = ""
                autor = ""
                data = ""

            snippet = (t[:120] + "…") if len(t) > 120 else t

            r["note_snippet"] = snippet
            r["note_full"] = t
            r["note_autor"] = autor
            r["note_data"] = data

            r["ilosc_dok"] = r.get("ilosc_dok", 0)
            r["sekundy_ksiegowosc"] = r.get("sekundy_ksiegowosc", 0)
            r["sekundy_kadry"] = r.get("sekundy_kadry", 0)

            result.append(r)

        result.sort(key=lambda item: polish_sort_key(item.get("nazwa") or ""))
        return result

    def format_seconds(self, sekundy: int) -> str:
        return TimerUtils.format_seconds(int(sekundy or 0))

    def get_running_seconds(self, typ: str) -> int:
        fields = self._TIMER_FIELDS.get(typ)
        if not fields:
            return 0
        start = getattr(self, fields[1])
        if not start:
            return 0
        return self._timer_elapsed(typ, start, datetime.datetime.now())

    def get_timer_total_seconds(self, firma_id: int, typ: str, saved_seconds: int) -> int:
        fields = self._TIMER_FIELDS.get(typ)
        if not fields or getattr(self, fields[0]) != firma_id:
            return int(saved_seconds or 0)
        return int(saved_seconds or 0) + self.get_running_seconds(typ)

    def get_active_timer_labels(self) -> List[str]:
        return [
            f"{item['task']} | {item['company']} - {item['department']} - {item['elapsed']}"
            for item in self.get_active_timer_details()
        ]

    def get_active_timer_details(self) -> List[Dict[str, str]]:
        details = []
        active_keys = set()
        for typ, label in (("ksiegowosc", "Księgowość"), ("kadry", "Kadry")):
            fields = self._TIMER_FIELDS[typ]
            firma_id = getattr(self, fields[0])
            if firma_id is None:
                continue
            activity = getattr(self, fields[2]) or {}
            key = (typ, firma_id, getattr(self, fields[1]), activity.get("task_id"), activity.get("task_title"))
            active_keys.add(key)
            cached = self._timer_label_cache.get(key)
            if cached is None:
                with get_conn() as conn:
                    company = conn.execute(
                        "SELECT COALESCE(NULLIF(nazwa_skrocona, ''), nazwa) AS nazwa FROM firmy WHERE id = %s",
                        (firma_id,),
                    ).fetchone()
                    task_name = activity.get("task_title")
                    if not task_name and activity.get("task_id"):
                        task = conn.execute("SELECT tytul FROM zadania WHERE id = %s", (activity["task_id"],)).fetchone()
                        task_name = task["tytul"] if task else None
                cached = {"task": task_name or label, "company": company["nazwa"] if company else "Nieznana firma", "department": label}
                self._timer_label_cache[key] = cached
            details.append({**cached, "elapsed": self.format_seconds(self.get_running_seconds(typ))})
        self._timer_label_cache = {key: value for key, value in self._timer_label_cache.items() if key in active_keys}
        general = self.get_general_timer_detail()
        if general:
            details.append(general)
        return details

    def save_status_for_company(
        self,
        firma_id: int,
        etap: Optional[str] = None,
        ilosc_dok: Optional[int] = None,
        uop: Optional[int] = None,
        uz: Optional[int] = None,
        uod: Optional[int] = None,
        dokumenty_dostarczone: Optional[int] = None,
        data_dostarczenia: Optional[str] = None,
        etap_kadry: Optional[str] = None,
    ):
        row = get_month_status(firma_id, self.aktualny_miesiac)
        payload: Dict[str, Any] = {}
        payload["dokumenty_dostarczone"] = (
            dokumenty_dostarczone
            if dokumenty_dostarczone is not None
            else (row or {}).get("dokumenty_dostarczone", 0)
        )
        if data_dostarczenia is not None:
            payload["data_dostarczenia"] = data_dostarczenia
        else:
            payload["data_dostarczenia"] = (row or {}).get("data_dostarczenia")
        if payload["dokumenty_dostarczone"] and not payload["data_dostarczenia"]:
            payload["data_dostarczenia"] = date.today().isoformat()

        if row:
            payload["etap"] = etap if etap is not None else row.get("etap", "Brak")
            payload["ilosc_dok"] = ilosc_dok if ilosc_dok is not None else row.get("ilosc_dok", 0)
            payload["sekundy_ksiegowosc"] = row.get("sekundy_ksiegowosc", 0)
            payload["sekundy_kadry"] = row.get("sekundy_kadry", 0)
            payload["wartosc_uslug"] = row.get("wartosc_uslug", 0.0)
        else:
            payload["etap"] = etap if etap is not None else "Brak"
            payload["ilosc_dok"] = ilosc_dok if ilosc_dok is not None else 0
            payload["sekundy_ksiegowosc"] = 0
            payload["sekundy_kadry"] = 0
            payload["wartosc_uslug"] = 0.0

        if etap_kadry is not None:
            payload["etap_kadry"] = etap_kadry

        if ilosc_dok is not None:
            payload['_manual_document_count'] = True
        save_month_status(firma_id, self.aktualny_miesiac, payload)

        if uop is not None or uz is not None or uod is not None:
            from app.db.dao_monthly import get_month_contracts, save_month_contracts
            current = get_month_contracts(firma_id, self.aktualny_miesiac)
            save_month_contracts(
                firma_id, self.aktualny_miesiac,
                int(uop if uop is not None else current["uop_ilosc"]),
                int(uz if uz is not None else current["uz_ilosc"]),
                int(uod if uod is not None else current["uod_ilosc"]),
            )

    def _ensure_month_status(self, firma_id: int):
        row = get_month_status(firma_id, self.aktualny_miesiac)
        if row is None:
            save_month_status(
                firma_id,
                self.aktualny_miesiac,
                {
                    "etap": "Brak",
                    "ilosc_dok": 0,
                    "sekundy_ksiegowosc": 0,
                    "sekundy_kadry": 0,
                    "wartosc_uslug": 0.0,
                },
            )

    def _finish_timer(self, typ: str, now: datetime.datetime, cur) -> None:
        fields = self._TIMER_FIELDS[typ]
        firma_id = getattr(self, fields[0])
        start = getattr(self, fields[1])
        if firma_id is None or start is None:
            return

        delta = self._timer_elapsed(typ, start, now)
        session = self._durable_timers.get(typ)
        month = getattr(session, 'month', '') or self.aktualny_miesiac
        dao_active_timers.finish(cur, session)
        cur.execute('INSERT INTO statusy_miesieczne (firma_id, miesiac) VALUES (%s, %s) '
                    'ON CONFLICT (firma_id, miesiac) DO NOTHING', (firma_id, month))
        cur.execute(
            f"""
            UPDATE statusy_miesieczne
            SET {fields[3]} = {fields[3]} + %s
            WHERE firma_id = %s AND miesiac = %s
            """,
            (delta, firma_id, month),
        )

        activity = getattr(self, fields[2])
        cur.execute(
            """
            INSERT INTO czasomierze (
                firma_id, pracownik_id, miesiac, typ, usluga_id, fakturowac,
                task_id, rozpoczecie, zakonczenie, sekundy
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                firma_id,
                self.pracownik_id,
                month,
                typ,
                (activity or {}).get("usluga_id"),
                1 if (activity or {}).get("fakturowac") else 0,
                (activity or {}).get("task_id"),
                start.isoformat(timespec="seconds"),
                now.isoformat(timespec="seconds"),
                delta,
            ),
        )
        if activity and activity.get("typ_czynnosci") == "extra" and activity.get("fakturowac"):
            add_invoice_item_for_service(
                firma_id,
                activity["usluga_id"],
                TimerUtils.round_seconds_to_minimum_half(delta),
                month,
                self.pracownik_id,
                zadanie_id=(activity or {}).get("task_id"),
                connection=cur.connection,
            )

        self._durable_timers.pop(typ, None)
        setattr(self, fields[0], None)
        setattr(self, fields[1], None)
        setattr(self, fields[2], None)

    def toggle_stoper(
            self,
            firma_id: int,
            typ: str,
            activity_payload: Optional[Dict[str, Any]] = None,
    ):
        fields = self._TIMER_FIELDS.get(typ)
        if not fields:
            raise ValueError(f"Nieznany typ stopera: {typ}")

        now = datetime.datetime.now()
        self._ensure_month_status(firma_id)
        self.stop_general_timer()
        saved_state = {field: getattr(self, field) for fields in self._TIMER_FIELDS.values() for field in fields[:3]}
        saved_sessions = dict(self._durable_timers)

        conn = get_conn()
        cur = conn.cursor()

        try:
            active_firma_id = getattr(self, fields[0])

            # Stop timer from the other department if running
            other_typ = "kadry" if typ == "ksiegowosc" else "ksiegowosc"
            other_fields = self._TIMER_FIELDS[other_typ]

            if getattr(self, other_fields[0]) is not None:
                self._finish_timer(other_typ, now, cur)

            # Clicking the same company should only STOP the timer
            if active_firma_id == firma_id:
                self._finish_timer(typ, now, cur)
                conn.commit()
                return

            # Stop currently running timer for this department
            if active_firma_id is not None:
                self._finish_timer(typ, now, cur)

            # Start timer for selected company
            task_id = (activity_payload or {}).get("task_id")
            if task_id:
                cur.execute(
                    """
                    UPDATE zadania
                    SET status = 'W toku',
                        updated_at = to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
                    WHERE id = %s
                      AND status = 'Nowe'
                    """,
                    (task_id,),
                )

            setattr(self, fields[0], firma_id)
            setattr(self, fields[1], now)
            setattr(self, fields[2], activity_payload)
            self._durable_timers[typ] = dao_active_timers.start(cur, self.pracownik_id, firma_id,
                self.aktualny_miesiac, typ, activity_payload, now)

            conn.commit()

        except Exception:
            conn.rollback()
            for field, value in saved_state.items():
                setattr(self, field, value)
            self._durable_timers = saved_sessions
            raise
        finally:
            conn.close()

    def open_notes_window(self, firma_id: int):
        from app.monthly_view.notes_window import NotesWindow
        NotesWindow(firma_id, self.pracownik_id).exec()

    def open_extra_service_window(self, firma_id: int):
        from app.monthly_view.monthly_view_ui import InlineExtraServiceWindow
        InlineExtraServiceWindow(firma_id, self.aktualny_miesiac).exec()

    def _get_company_data(self, firma_id: int) -> Optional[Dict[str, Any]]:
        row = get_company(firma_id)
        return dict(row) if row else None

    def _save_company_data(self, firma_id: Optional[int], payload: Dict[str, Any]):
        data = dict(payload)
        if firma_id:
            data["id"] = firma_id
        save_company(data, actor_id=self.pracownik_id)

    def _assert_company_access(self, firma_id: int, permission: str = "can_edit_companies"):
        if is_administrator(self.pracownik_id):
            return
        if firma_id not in assigned_company_ids(self.pracownik_id):
            raise PermissionError("Brak dostępu do tej firmy.")
        if not can(self.pracownik_id, permission):
            raise PermissionError("Brak uprawnień do edycji firm.")

    def open_company_edit_window(self, firma_id: int):
        from app.companies.company_form import CompanyForm

        self._assert_company_access(firma_id)
        CompanyForm(firma_id, actor_id=self.pracownik_id).exec()

    def mark_company_suspended_manual(self, firma_id: int):
        self.toggle_services_suspended(firma_id, True)

    def toggle_services_suspended(self, firma_id: int, suspended: bool):
        from app.db.dao_companies import set_services_suspended
        set_services_suspended(firma_id, suspended, actor_id=self.pracownik_id)

    def check_vat_status(self, firma_id: int):
        from app.utils.vat_check import check_company_vat
        self._assert_company_access(firma_id)
        return check_company_vat(firma_id, actor_id=self.pracownik_id)

    def check_ceidg_suspension(self, firma_id: int):
        from app.utils.ceidg_suspension import check_company_ceidg
        self._assert_company_access(firma_id)
        return check_company_ceidg(firma_id)

    def move_company_to_archive(self, firma_id: int):
        self._assert_company_access(firma_id)
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE firmy
            SET status = 'Archiwalne'
            WHERE id = %s
        """,
            (firma_id,),
        )
        conn.commit()
        conn.close()

    def more_option_1(self, firma_id: int):
        print("Opcja 1:", firma_id)

    def more_option_2(self, firma_id: int):
        print("Opcja 2:", firma_id)

    def more_option_3(self, firma_id: int):
        print("Opcja 3:", firma_id)

    def _auto_check_suspension(self):
        from app.utils.ceidg_suspension import run_in_background
        run_in_background()
    def _last_day_of_month(self, d: date) -> int:
        return monthrange(d.year, d.month)[1]
