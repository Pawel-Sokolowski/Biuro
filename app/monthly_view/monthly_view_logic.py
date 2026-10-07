import sqlite3
import datetime
from typing import List, Dict, Any, Optional

from app.db.dao_conn import get_conn
from app.db.dao_monthly import get_month_status, save_month_status
from app.db.dao_companies import get_company, save_company
from app.global_utils.calculator import oblicz_miesieczny_koszt


class MonthlyViewLogic:
    def __init__(self, pracownik_id: int):
        self.pracownik_id = pracownik_id
        self.aktualny_miesiac = datetime.datetime.now().strftime("%Y-%m")

        # stopery
        self.aktywna_firma_stoper_ks = None
        self.aktywna_firma_stoper_kadry = None
        self.czas_start_ks = None
        self.czas_start_kadry = None
        self.timer_month = None

        # ui
        self.status_widgets = {}

        # auto-check zawieszenia
        self._auto_check_suspension()

    # zmiana miesiąca
    def set_month(self, new_month: str):
        self.aktualny_miesiac = new_month

    # lista pracowników
    def get_employee_filter_list(self) -> List:
        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("""
            SELECT id, imie || ' ' || nazwisko AS label
            FROM pracownicy
            WHERE aktywny = 1
            ORDER BY nazwisko, imie
        """)

        rows = cur.fetchall()
        conn.close()

        result = [(0, "Wszyscy")]
        result.extend([(r["id"], r["label"]) for r in rows])
        return result
    # główna lista miesięczna
    def get_monthly_rows(self, selected_month: str, selected_employee: Optional[int], selected_status: str) -> List[Dict[str, Any]]:
        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("""
            SELECT f.id, f.nazwa, f.status, f.zawieszenia_dzialalnosci,
                   COALESCE(sm.etap, 'Brak') AS etap,
                   COALESCE(sm.ilosc_dok, 0) AS ilosc_dok,
                   COALESCE(sm.sekundy_ksiegowosc, 0) AS sekundy_ksiegowosc,
                   COALESCE(sm.sekundy_kadry, 0) AS sekundy_kadry
            FROM firmy f
            LEFT JOIN statusy_miesieczne sm
                ON sm.firma_id = f.id AND sm.miesiac = ?
            ORDER BY f.nazwa
        """, (selected_month,))

        rows = [dict(r) for r in cur.fetchall()]
        conn.close()

        result = []

        for r in rows:
            # filtr statusu
            if selected_status != "Wszystkie":
                if (r["status"] or "Aktywne") != selected_status:
                    continue

            # filtr pracownika
            if selected_employee:
                conn2 = get_conn()
                cur2 = conn2.cursor()
                cur2.execute("""
                    SELECT 1 FROM pracownik_firma
                    WHERE firma_id = ? AND pracownik_id = ?
                """, (r["id"], selected_employee))
                assigned = cur2.fetchone()
                conn2.close()

                if not assigned:
                    continue

            # notatka
            conn3 = get_conn()
            cur3 = conn3.cursor()
            cur3.execute("""
                SELECT tresc
                FROM company_notes
                WHERE firma_id = ?
                ORDER BY data_utworzenia DESC LIMIT 1
            """, (r["id"],))
            note_row = cur3.fetchone()
            conn3.close()

            if note_row:
                t = note_row["tresc"]
                snippet = (t[:120] + "…") if len(t) > 120 else t
            else:
                snippet = ""

            r["note_snippet"] = snippet
            r["zawieszona_od"] = r.get("zawieszenia_dzialalnosci") or ""

            if selected_month == self.timer_month:
                if r["id"] == self.aktywna_firma_stoper_ks and self.czas_start_ks:
                    r["sekundy_ksiegowosc"] += self._running_seconds(self.czas_start_ks)
                if r["id"] == self.aktywna_firma_stoper_kadry and self.czas_start_kadry:
                    r["sekundy_kadry"] += self._running_seconds(self.czas_start_kadry)

            result.append(r)

        return result

    # format czasu
    def format_seconds(self, sekundy: int) -> str:
        return str(datetime.timedelta(seconds=sekundy or 0))

    @staticmethod
    def _running_seconds(start_time: datetime.datetime) -> int:
        return max(0, int((datetime.datetime.now() - start_time).total_seconds()))

    # autosave
    def schedule_status_autosave(self, firma_id: int):
        docs_widget, etap_widget = self.status_widgets[firma_id]
        payload = {
            "etap": etap_widget.currentText(),
            "ilosc_dok": int(docs_widget.text()) if docs_widget.text().isdigit() else 0,
        }
        save_month_status(firma_id, self.aktualny_miesiac, payload)

    # ensure status
    def _ensure_month_status(self, firma_id: int):
        row = get_month_status(firma_id, self.aktualny_miesiac)
        if row is None:
            save_month_status(firma_id, self.aktualny_miesiac, {
                "etap": "Brak",
                "ilosc_dok": 0,
                "sekundy_ksiegowosc": 0,
                "sekundy_kadry": 0,
                "wartosc_uslug": 0.0
            })

    def get_monthly_status(self, month: str, typ: str, status: str, emp_id: Optional[int]):
        return self.get_monthly_rows(month, emp_id, status)

    # unified stoper
    def toggle_stoper(self, firma_id: int, typ: str):
        if typ not in ("ksiegowosc", "kadry"):
            raise ValueError(f"Unsupported timer type: {typ}")

        now = datetime.datetime.now()
        self._ensure_month_status(firma_id)

        conn = get_conn()
        cur = conn.cursor()

        active_type = self._active_timer_type()
        active_firma_id = self._active_timer_company()

        if active_type and active_firma_id == firma_id and active_type == typ:
            self._persist_active_timer(cur, now)
            conn.commit()
            conn.close()
            self._clear_active_timer()
            return

        if active_type:
            self._persist_active_timer(cur, now)
            self._clear_active_timer()

        if typ == "ksiegowosc":
            self.aktywna_firma_stoper_ks = firma_id
            self.czas_start_ks = now
        else:
            self.aktywna_firma_stoper_kadry = firma_id
            self.czas_start_kadry = now
        self.timer_month = self.aktualny_miesiac

        conn.commit()
        conn.close()

    def _active_timer_type(self) -> Optional[str]:
        if self.aktywna_firma_stoper_ks is not None:
            return "ksiegowosc"
        if self.aktywna_firma_stoper_kadry is not None:
            return "kadry"
        return None

    def _active_timer_company(self) -> Optional[int]:
        if self.aktywna_firma_stoper_ks is not None:
            return self.aktywna_firma_stoper_ks
        return self.aktywna_firma_stoper_kadry

    def _persist_active_timer(self, cur, now: datetime.datetime):
        timer_type = self._active_timer_type()
        firma_id = self._active_timer_company()
        if not timer_type or firma_id is None:
            return

        start_time = self.czas_start_ks if timer_type == "ksiegowosc" else self.czas_start_kadry
        timer_month = self.timer_month
        seconds_column = (
            "sekundy_ksiegowosc" if timer_type == "ksiegowosc" else "sekundy_kadry"
        )
        delta = max(0, int((now - start_time).total_seconds()))
        cur.execute(
            f"""
            UPDATE statusy_miesieczne
            SET {seconds_column} = {seconds_column} + ?
            WHERE firma_id = ? AND miesiac = ?
            """,
            (delta, firma_id, timer_month),
        )

    def _clear_active_timer(self):
        self.aktywna_firma_stoper_ks = None
        self.aktywna_firma_stoper_kadry = None
        self.czas_start_ks = None
        self.czas_start_kadry = None
        self.timer_month = None
    # okna
    # def open_notes_window(self, firma_id: int):
    #     from app.monthly_view.monthly_view_ui import InlineNotesWindow
    #     InlineNotesWindow(firma_id).exec()
    #
    # def open_extra_service_window(self, firma_id: int):
    #     from app.monthly_view.monthly_view_ui import InlineExtraServiceWindow
    #     InlineExtraServiceWindow(firma_id, self.aktualny_miesiac).exec()

    # firma helpers
    def _get_company_data(self, firma_id: int) -> Optional[Dict[str, Any]]:
        row = get_company(firma_id)
        return dict(row) if row else None

    def _save_company_data(self, firma_id: Optional[int], payload: Dict[str, Any]):
        data = dict(payload)
        if firma_id:
            data["id"] = firma_id
        save_company(data)

    def open_company_edit_window(self, firma_id: int):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout
        from app.companies.company_form import CompanyForm

        company_data = self._get_company_data(firma_id)

        dialog = QDialog()
        dialog.setWindowTitle("Edycja firmy")
        dialog.setModal(True)
        dialog.resize(700, 600)

        layout = QVBoxLayout(dialog)
        form = CompanyForm(self.pracownik_id, company_data, parent=dialog)

        def _on_saved():
            dialog.accept()

        form.on_saved = _on_saved
        layout.addWidget(form)
        dialog.exec()

    # zawieszenia — ręczne
    def mark_company_suspended_manual(self, firma_id: int):
        self.set_company_status(firma_id, "Zawieszone")

    def set_company_status(self, firma_id: int, status: str):
        if status not in ("Aktywne", "Zawieszone", "Archiwalne"):
            raise ValueError(f"Unsupported company status: {status}")

        suspended_at = (
            datetime.date.today().strftime("%Y-%m-%d")
            if status == "Zawieszone"
            else None
        )
        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
            UPDATE firmy
            SET status = ?,
                zawieszenia_dzialalnosci = ?
            WHERE id = ?
        """, (status, suspended_at, firma_id))

        conn.commit()
        conn.close()

    # przeniesienie do archiwalnych
    def move_company_to_archive(self, firma_id: int):
        self.set_company_status(firma_id, "Archiwalne")

    # opcje dodatkowe
    def more_option_1(self, firma_id: int):
        print("Opcja 1:", firma_id)

    def more_option_2(self, firma_id: int):
        print("Opcja 2:", firma_id)

    def more_option_3(self, firma_id: int):
        print("Opcja 3:", firma_id)

    # auto-check zawieszenia 1 i ostatniego dnia miesiąca
    def _auto_check_suspension(self):
        today = datetime.date.today()
        day = today.day
        last_day = self._last_day_of_month(today)

        # tylko 1 dzień miesiąca lub ostatni
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
                cur2.execute("""
                    UPDATE firmy
                    SET status = 'Zawieszone',
                        zawieszenia_dzialalnosci = ?
                    WHERE id = ?
                """, (zaw, firma_id))
                conn.commit()

        conn.close()


    def _last_day_of_month(self, d: datetime.date) -> int:
        next_month = d.replace(day=28) + datetime.timedelta(days=4)
        return (next_month - datetime.timedelta(days=next_month.day)).day
