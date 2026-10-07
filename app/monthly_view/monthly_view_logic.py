from __future__ import annotations

import datetime
import sqlite3
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from app.db.dao_conn import get_conn
from app.db.dao_monthly import get_month_status, save_month_status
from app.db.dao_invoices import add_invoice_item_for_service
from app.db.dao_companies import get_company, save_company
from app.db.dao_notes import get_latest_notes_for_firms
from app.utils.timer import TimerUtils


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
        self.aktualny_miesiac = datetime.datetime.now().strftime("%Y-%m")

        self.aktywna_firma_stoper_ks: Optional[int] = None
        self.aktywna_firma_stoper_kadry: Optional[int] = None
        self.czas_start_ks: Optional[datetime.datetime] = None
        self.czas_start_kadry: Optional[datetime.datetime] = None

        self.aktywna_firma_stoper_ks_activity: Optional[Dict[str, Any]] = None
        self.aktywna_firma_stoper_kadry_activity: Optional[Dict[str, Any]] = None

        self._auto_check_suspension()

    def set_month(self, new_month: str):
        self.aktualny_miesiac = new_month

    def get_employee_filter_list(self) -> List:
        conn = get_conn()
        conn.row_factory = sqlite3.Row
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
        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute(
            """
            SELECT f.id, f.nazwa, f.status,
                   COALESCE(sm.etap, 'Brak') AS etap,
                   CASE
                       WHEN (
                           SELECT COUNT(*)
                           FROM dokumenty d
                           WHERE d.firma_id = f.id
                             AND substr(d.data_wyst, 1, 7) = ?
                       ) > 0
                       THEN (
                           SELECT COUNT(*)
                           FROM dokumenty d
                           WHERE d.firma_id = f.id
                             AND substr(d.data_wyst, 1, 7) = ?
                       )
                       ELSE COALESCE(sm.ilosc_dok, 0)
                   END AS ilosc_dok,
                   COALESCE(sm.sekundy_ksiegowosc, 0) AS sekundy_ksiegowosc,
                   COALESCE(sm.sekundy_kadry, 0) AS sekundy_kadry
            FROM firmy f
            LEFT JOIN statusy_miesieczne sm
                ON sm.firma_id = f.id AND sm.miesiac = ?
            ORDER BY f.nazwa
        """,
            (selected_month, selected_month, selected_month),
        )

        rows = [dict(r) for r in cur.fetchall()]
        conn.close()

        result: List[Dict[str, Any]] = []

        assigned_map: Dict[int, bool] = {}
        if selected_employee:
            conn2 = get_conn()
            conn2.row_factory = sqlite3.Row
            cur2 = conn2.cursor()
            cur2.execute(
                """
                SELECT firma_id FROM pracownik_firma WHERE pracownik_id = ?
            """,
                (selected_employee,),
            )
            for r in cur2.fetchall():
                assigned_map[r["firma_id"]] = True
            conn2.close()

        firm_ids = [r["id"] for r in rows]
        notes_map: Dict[int, Dict[str, Any]] = {}
        if firm_ids:
            notes_map = get_latest_notes_for_firms(firm_ids)

        for r in rows:
            if selected_status != "Wszystkie":
                if (r["status"] or "Aktywne") != selected_status:
                    continue

            if selected_employee:
                if not assigned_map.get(r["id"]):
                    continue

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
        return max(0, int((datetime.datetime.now() - start).total_seconds()))

    def get_timer_total_seconds(self, firma_id: int, typ: str, saved_seconds: int) -> int:
        fields = self._TIMER_FIELDS.get(typ)
        if not fields or getattr(self, fields[0]) != firma_id:
            return int(saved_seconds or 0)
        return int(saved_seconds or 0) + self.get_running_seconds(typ)

    def get_active_timer_labels(self) -> List[str]:
        labels: List[str] = []
        for typ, label in (("ksiegowosc", "Księgowość"), ("kadry", "Kadry")):
            fields = self._TIMER_FIELDS[typ]
            if getattr(self, fields[0]) is not None:
                labels.append(f"{label}: {self.format_seconds(self.get_running_seconds(typ))}")
        return labels

    def save_status_for_company(
        self,
        firma_id: int,
        etap: Optional[str] = None,
        ilosc_dok: Optional[int] = None,
        uop: Optional[int] = None,
        uz: Optional[int] = None,
        uod: Optional[int] = None,
    ):
        row = get_month_status(firma_id, self.aktualny_miesiac)
        payload: Dict[str, Any] = {}

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

        save_month_status(firma_id, self.aktualny_miesiac, payload)

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

        delta = max(0, int((now - start).total_seconds()))
        cur.execute(
            f"""
            UPDATE statusy_miesieczne
            SET {fields[3]} = {fields[3]} + ?
            WHERE firma_id = ? AND miesiac = ?
            """,
            (delta, firma_id, self.aktualny_miesiac),
        )

        activity = getattr(self, fields[2])
        cur.execute(
            """
            INSERT INTO czasomierze (
                firma_id, pracownik_id, miesiac, typ, usluga_id, fakturowac,
                rozpoczecie, zakonczenie, sekundy
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                firma_id,
                self.pracownik_id,
                self.aktualny_miesiac,
                typ,
                (activity or {}).get("usluga_id"),
                1 if (activity or {}).get("fakturowac") else 0,
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
                self.aktualny_miesiac,
                self.pracownik_id,
            )

        setattr(self, fields[0], None)
        setattr(self, fields[1], None)
        setattr(self, fields[2], None)

    def toggle_stoper(
        self, firma_id: int, typ: str, activity_payload: Optional[Dict[str, Any]] = None
    ):
        fields = self._TIMER_FIELDS.get(typ)
        if not fields:
            raise ValueError(f"Nieznany typ stopera: {typ}")

        now = datetime.datetime.now()
        self._ensure_month_status(firma_id)

        conn = get_conn()
        cur = conn.cursor()
        active_firma_id = getattr(self, fields[0])

        # A single department timer may run at a time. Starting one department
        # always commits and stops the timer from the other department first.
        other_typ = "kadry" if typ == "ksiegowosc" else "ksiegowosc"
        other_fields = self._TIMER_FIELDS[other_typ]
        if getattr(self, other_fields[0]) is not None:
            self._finish_timer(other_typ, now, cur)

        if active_firma_id is not None:
            self._finish_timer(typ, now, cur)

        if active_firma_id != firma_id:
            setattr(self, fields[0], firma_id)
            setattr(self, fields[1], now)
            setattr(self, fields[2], activity_payload)

        conn.commit()
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
        save_company(data)
    def open_company_edit_window(self, firma_id: int):
        from app.companies.company_form import CompanyForm

        CompanyForm(firma_id).exec()

    def mark_company_suspended_manual(self, firma_id: int):
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE firmy
            SET status = 'Zawieszone',
                zawieszenia_dzialalnosci = ?
            WHERE id = ?
        """,
            (today, firma_id),
        )
        conn.commit()
        conn.close()

    def move_company_to_archive(self, firma_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE firmy
            SET status = 'Archiwalne'
            WHERE id = ?
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
        today = date.today()
        day = today.day
        last_day = self._last_day_of_month(today)

        if day not in (1, last_day):
            return

        from app.companies.ceidg_api import get_ceidg_firma_by_nip

        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("SELECT id, nip FROM firmy")
        rows = cur.fetchall()

        for r in rows:
            firma_id = r["id"]
            nip = r["nip"]

            if not nip:
                continue

            data = get_ceidg_firma_by_nip(nip)
            if not data or data.get("error"):
                continue

            status = (data.get("status") or "").upper()
            zaw = data.get("zawieszenia_dzialalnosci") or ""

            if status == "ZAWIESZONA":
                cur2 = conn.cursor()
                cur2.execute(
                    """
                    UPDATE firmy
                    SET status = 'Zawieszone',
                        zawieszenia_dzialalnosci = ?
                    WHERE id = ?
                """,
                    (zaw, firma_id),
                )
                conn.commit()

        conn.close()

    def _last_day_of_month(self, d: date) -> int:
        next_month = d.replace(day=28) + timedelta(days=4)
        return (next_month - timedelta(days=next_month.day)).day
