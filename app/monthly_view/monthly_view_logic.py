# app/monthly_view/monthly_view_logic.py
import datetime
import sqlite3
from datetime import date, timedelta
from typing import Optional, Dict, Any, List

from app.db.dao_conn import get_conn
from app.db.dao_companies import get_company, save_company
from app.db.dao_monthly import get_month_status, save_month_status


class MonthlyViewLogic:
    def __init__(self, pracownik_id: int):
        self.pracownik_id = pracownik_id
        self.aktualny_miesiac = datetime.datetime.now().strftime("%Y-%m")

        # stopery
        self.aktywna_firma_stoper_ks = None
        self.aktywna_firma_stoper_kadry = None
        self.czas_start_ks = None
        self.czas_start_kadry = None

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

            result.append(r)

        return result
    # format czasu
    def format_seconds(self, sekundy: int) -> str:
        return str(datetime.timedelta(seconds=sekundy or 0))

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

    # unified stoper
    def toggle_stoper(self, firma_id: int, typ: str):
        now = datetime.datetime.now()
        self._ensure_month_status(firma_id)

        conn = get_conn()
        cur = conn.cursor()

        if typ == "ksiegowosc":
            if self.aktywna_firma_stoper_ks is None:
                self.aktywna_firma_stoper_ks = firma_id
                self.czas_start_ks = now
                conn.close()
                return

            if self.aktywna_firma_stoper_ks == firma_id:
                delta = int((now - self.czas_start_ks).total_seconds())
                cur.execute("""
                    UPDATE statusy_miesieczne
                    SET sekundy_ksiegowosc = sekundy_ksiegowosc + ?
                    WHERE firma_id = ? AND miesiac = ?
                """, (delta, firma_id, self.aktualny_miesiac))
                conn.commit()
                conn.close()
                self.aktywna_firma_stoper_ks = None
                self.czas_start_ks = None
                return

            prev = self.aktywna_firma_stoper_ks
            delta_prev = int((now - self.czas_start_ks).total_seconds())
            cur.execute("""
                UPDATE statusy_miesieczne
                SET sekundy_ksiegowosc = sekundy_ksiegowosc + ?
                WHERE firma_id = ? AND miesiac = ?
            """, (delta_prev, prev, self.aktualny_miesiac))

            self.aktywna_firma_stoper_ks = firma_id
            self.czas_start_ks = now
            conn.commit()
            conn.close()
            return

        if typ == "kadry":
            if self.aktywna_firma_stoper_kadry is None:
                self.aktywna_firma_stoper_kadry = firma_id
                self.czas_start_kadry = now
                conn.close()
                return

            if self.aktywna_firma_stoper_kadry == firma_id:
                delta = int((now - self.czas_start_kadry).total_seconds())
                cur.execute("""
                    UPDATE statusy_miesieczne
                    SET sekundy_kadry = sekundy_kadry + ?
                    WHERE firma_id = ? AND miesiac = ?
                """, (delta, firma_id, self.aktualny_miesiac))
                conn.commit()
                conn.close()
                self.aktywna_firma_stoper_kadry = None
                self.czas_start_kadry = None
                return

            prev = self.aktywna_firma_stoper_kadry
            delta_prev = int((now - self.czas_start_kadry).total_seconds())
            cur.execute("""
                UPDATE statusy_miesieczne
                SET sekundy_kadry = sekundy_kadry + ?
                WHERE firma_id = ? AND miesiac = ?
            """, (delta_prev, prev, self.aktualny_miesiac))

            self.aktywna_firma_stoper_kadry = firma_id
            self.czas_start_kadry = now
            conn.commit()
            conn.close()
            return

    # okna
    def open_notes_window(self, firma_id: int):
        from app.monthly_view.monthly_view_ui import InlineNotesWindow
        InlineNotesWindow(firma_id).exec()

    def open_extra_service_window(self, firma_id: int):
        from app.monthly_view.monthly_view_ui import InlineExtraServiceWindow
        InlineExtraServiceWindow(firma_id, self.aktualny_miesiac).exec()

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
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
            UPDATE firmy
            SET status = 'Zawieszone',
                zawieszenia_dzialalnosci = ?
            WHERE id = ?
        """, (today, firma_id))

        conn.commit()
        conn.close()

    # przeniesienie do archiwalnych
    def move_company_to_archive(self, firma_id: int):
        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
            UPDATE firmy
            SET status = 'Archiwalne'
            WHERE id = ?
        """, (firma_id,))

        conn.commit()
        conn.close()

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

    # ostatni dzień miesiąca
    def _last_day_of_month(self, d: date) -> int:
        next_month = d.replace(day=28) + timedelta(days=4)
        return (next_month - timedelta(days=next_month.day)).day
