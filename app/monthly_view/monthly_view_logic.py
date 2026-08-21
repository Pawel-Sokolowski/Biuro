# app/logic/monthly_view_logic.py
import datetime
from typing import Optional, List, Dict, Any

from PyQt6.QtCore import QTimer

from app.db.dao_conn import get_conn
from app.global_utils.settings import is_admin


class MonthlyViewLogic:
    def __init__(self, pracownik_id: int):
        self.pracownik_id = pracownik_id

        # aktualny miesiąc
        self.aktualny_miesiac = datetime.datetime.now().strftime("%Y-%m")

        # stoper
        self.aktywna_firma_stoper: Optional[int] = None
        self.aktywna_firma_stoper_nazwa: str = ""
        self.czas_start: Optional[datetime.datetime] = None

        # autosave
        self.status_autosave_timers: Dict[int, QTimer] = {}
        self.status_widgets: Dict[int, tuple] = {}

    # ============================================================
    # EMPLOYEE FILTER
    # ============================================================
    def get_employee_filter_list(self) -> List[tuple]:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, imie, nazwisko
            FROM pracownicy
            WHERE aktywny = 1
            ORDER BY nazwisko, imie
        """)
        rows = cur.fetchall()
        conn.close()

        return [(r["id"], f"{r['imie']} {r['nazwisko']}") for r in rows]

    # ============================================================
    # MONTH
    # ============================================================
    def set_month(self, new_month: str):
        self.aktualny_miesiac = new_month

    # ============================================================
    # MAIN TABLE DATA
    # ============================================================
    def get_monthly_rows(self, selected_month: str, selected_employee: Optional[int]) -> List[Dict[str, Any]]:
        conn = get_conn()
        cur = conn.cursor()

        # ADMIN
        if is_admin(self.pracownik_id):
            if selected_employee:
                cur.execute("""
                    SELECT f.id, f.nazwa, COALESCE(sm.etap, 'Brak'),
                           COALESCE(sm.ilosc_dok, 0), COALESCE(f.sekundy_pracy, 0)
                    FROM firmy f
                    JOIN pracownik_firma pf ON pf.firma_id = f.id
                    LEFT JOIN statusy_miesieczne sm ON sm.firma_id = f.id AND sm.miesiac = ?
                    WHERE pf.pracownik_id = ?
                    GROUP BY f.id
                    ORDER BY f.nazwa
                """, (selected_month, selected_employee))
            else:
                cur.execute("""
                    SELECT f.id, f.nazwa, COALESCE(sm.etap, 'Brak'),
                           COALESCE(sm.ilosc_dok, 0), COALESCE(f.sekundy_pracy, 0)
                    FROM firmy f
                    LEFT JOIN statusy_miesieczne sm ON sm.firma_id = f.id AND sm.miesiac = ?
                    ORDER BY f.nazwa
                """, (selected_month,))
        else:
            cur.execute("""
                SELECT f.id, f.nazwa, COALESCE(sm.etap, 'Brak'),
                       COALESCE(sm.ilosc_dok, 0), COALESCE(f.sekundy_pracy, 0)
                FROM firmy f
                JOIN pracownik_firma pf ON pf.firma_id = f.id
                LEFT JOIN statusy_miesieczne sm ON sm.firma_id = f.id AND sm.miesiac = ?
                WHERE pf.pracownik_id = ?
                GROUP BY f.id
                ORDER BY f.nazwa
            """, (selected_month, self.pracownik_id))

        rows = cur.fetchall()
        conn.close()

        # notatki
        result = []
        for r in rows:
            f_id, nazwa, etap, ilosc_dok, sekundy = r

            conn2 = get_conn()
            cur2 = conn2.cursor()
            cur2.execute("""
                SELECT tresc
                FROM company_notes
                WHERE firma_id = ?
                ORDER BY data_utworzenia DESC LIMIT 1
            """, (f_id,))
            note_row = cur2.fetchone()
            conn2.close()

            if note_row:
                tresc = note_row["tresc"]
                snippet = (tresc[:120] + "…") if len(tresc) > 120 else tresc
            else:
                snippet = ""

            result.append({
                "id": f_id,
                "nazwa": nazwa,
                "etap": etap,
                "ilosc_dok": ilosc_dok,
                "sekundy": sekundy,
                "note_snippet": snippet
            })

        return result

    # ============================================================
    # FORMAT SECONDS
    # ============================================================
    def format_seconds(self, sekundy: int) -> str:
        return str(datetime.timedelta(seconds=sekundy or 0))

    # ============================================================
    # STOPER
    # ============================================================
    def toggle_stoper(self, firma_id: int, btn):
        conn = get_conn()
        cur = conn.cursor()

        # start
        if self.aktywna_firma_stoper is None:
            self.aktywna_firma_stoper = firma_id
            self.czas_start = datetime.datetime.now()

            cur.execute("SELECT nazwa FROM firmy WHERE id=?", (firma_id,))
            res = cur.fetchone()
            self.aktywna_firma_stoper_nazwa = res["nazwa"] if res else str(firma_id)

            btn.setText("⏸")
            btn.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold;")

            conn.close()
            return

        # stop
        if self.aktywna_firma_stoper == firma_id:
            delta = int((datetime.datetime.now() - self.czas_start).total_seconds())
            cur.execute("UPDATE firmy SET sekundy_pracy = sekundy_pracy + ? WHERE id = ?", (delta, firma_id))
            conn.commit()
            conn.close()

            self.aktywna_firma_stoper = None
            self.aktywna_firma_stoper_nazwa = ""
            self.czas_start = None

            btn.setText("▶")
            btn.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold;")
            return

        # switch company
        prev_id = self.aktywna_firma_stoper
        delta_prev = int((datetime.datetime.now() - self.czas_start).total_seconds())
        cur.execute("UPDATE firmy SET sekundy_pracy = sekundy_pracy + ? WHERE id = ?", (delta_prev, prev_id))

        self.aktywna_firma_stoper = firma_id
        self.czas_start = datetime.datetime.now()

        cur.execute("SELECT nazwa FROM firmy WHERE id=?", (firma_id,))
        res = cur.fetchone()
        self.aktywna_firma_stoper_nazwa = res["nazwa"] if res else str(firma_id)

        conn.commit()
        conn.close()

    # ============================================================
    # NOTES
    # ============================================================
    def open_notes_window(self, firma_id: int):
        from app.main_ui import NotesWindow
        dlg = NotesWindow(firma_id)
        dlg.exec()

    # ============================================================
    # EXTRA SERVICES
    # ============================================================
    def open_extra_service_window(self, firma_id: int):
        from app.services.extra_service_ui import ExtraServiceWindow
        dlg = ExtraServiceWindow(firma_id, self.aktualny_miesiac)
        dlg.exec()

    # ============================================================
    # COMPANY EDIT
    # ============================================================
    def open_company_edit_window(self, firma_id: int):
        from app.companies.companies_ui import CompanyDialog
        dlg = CompanyDialog(firma_id)
        dlg.exec()

    # ============================================================
    # AUTOSAVE
    # ============================================================
    def schedule_status_autosave(self, firma_id: int):
        timer = self.status_autosave_timers.get(firma_id)
        if timer is None:
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(lambda fid=firma_id: self.save_status_row(fid))
            self.status_autosave_timers[firma_id] = timer

        timer.start(700)

    def save_status_row(self, firma_id: int):
        txt_widget, cb_widget = self.status_widgets.get(firma_id, (None, None))

        ilosc_dok = txt_widget.text() if txt_widget else "0"
        etap = cb_widget.currentText() if cb_widget else "Brak"

        try:
            ilosc_dok = int(ilosc_dok)
        except ValueError:
            return

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO statusy_miesieczne (firma_id, miesiac, etap, ilosc_dok)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(firma_id, miesiac)
            DO UPDATE SET etap = excluded.etap, ilosc_dok = excluded.ilosc_dok
        """, (firma_id, self.aktualny_miesiac, etap, ilosc_dok))
        conn.commit()
        conn.close()
