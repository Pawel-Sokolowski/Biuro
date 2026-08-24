# app/monthly_view/monthly_view_logic.py
import datetime
from typing import Optional, Dict, Any, List
from app.db.dao_conn import get_conn
from app.db.dao_companies import get_month_status, save_month_status
from app.global_utils.calculator import oblicz_miesieczny_koszt


class MonthlyViewLogic:
    def __init__(self, pracownik_id: int):
        self.pracownik_id = pracownik_id
        self.aktualny_miesiac = datetime.datetime.now().strftime("%Y-%m")

        # STOPERY
        self.aktywna_firma_stoper_ks = None
        self.aktywna_firma_stoper_kadry = None

        self.czas_start_ks = None
        self.czas_start_kadry = None

        # UI helpers
        self.status_widgets = {}

    # ============================================================
    # MONTH SETTER
    # ============================================================
    def set_month(self, new_month: str):
        self.aktualny_miesiac = new_month

    # ============================================================
    # EMPLOYEE FILTER LIST
    # ============================================================
    def get_employee_filter_list(self) -> List:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, imie || ' ' || nazwisko AS label
            FROM pracownicy
            WHERE aktywny = 1
            ORDER BY nazwisko, imie
        """)
        rows = cur.fetchall()
        conn.close()
        return [(r["id"], r["label"]) for r in rows]

    # ============================================================
    # MONTHLY ROWS
    # ============================================================
    def get_monthly_rows(self, selected_month: str, selected_employee: Optional[int]) -> List[Dict[str, Any]]:
        conn = get_conn()
        cur = conn.cursor()

        # ADMIN widzi wszystko
        cur.execute("""
            SELECT f.id, f.nazwa,
                   COALESCE(sm.etap, 'Brak') AS etap,
                   COALESCE(sm.ilosc_dok, 0) AS ilosc_dok,
                   COALESCE(sm.sekundy_ksiegowosc, 0) AS sekundy_ksiegowosc,
                   COALESCE(sm.sekundy_kadry, 0) AS sekundy_kadry
            FROM firmy f
            LEFT JOIN statusy_miesieczne sm
                ON sm.firma_id = f.id AND sm.miesiac = ?
            ORDER BY f.nazwa
        """, (selected_month,))

        rows = cur.fetchall()
        conn.close()

        # notatki
        result = []
        for r in rows:
            f_id = r["id"]

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
                "nazwa": r["nazwa"],
                "etap": r["etap"],
                "ilosc_dok": r["ilosc_dok"],
                "sekundy_ksiegowosc": r["sekundy_ksiegowosc"],
                "sekundy_kadry": r["sekundy_kadry"],
                "note_snippet": snippet
            })

        return result

    # ============================================================
    # FORMAT SECONDS
    # ============================================================
    def format_seconds(self, sekundy: int) -> str:
        return str(datetime.timedelta(seconds=sekundy or 0))

    # ============================================================
    # AUTO-SAVE STATUS
    # ============================================================
    def schedule_status_autosave(self, firma_id: int):
        docs_widget, etap_widget = self.status_widgets[firma_id]

        payload = {
            "etap": etap_widget.currentText(),
            "ilosc_dok": int(docs_widget.text()) if docs_widget.text().isdigit() else 0,
        }

        save_month_status(firma_id, self.aktualny_miesiac, payload)

    # ============================================================
    # ENSURE MONTH STATUS EXISTS
    # ============================================================
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

    # ============================================================
    # STOPER — unified handler
    # ============================================================
    def toggle_stoper(self, firma_id: int, typ: str):
        now = datetime.datetime.now()
        self._ensure_month_status(firma_id)

        conn = get_conn()
        cur = conn.cursor()

        # ============================
        # KSIĘGOWOŚĆ
        # ============================
        if typ == "ksiegowosc":

            # start
            if self.aktywna_firma_stoper_ks is None:
                self.aktywna_firma_stoper_ks = firma_id
                self.czas_start_ks = now
                conn.close()
                return

            # stop
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

            # switch
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

        # ============================
        # KADRY
        # ============================
        if typ == "kadry":

            # start
            if self.aktywna_firma_stoper_kadry is None:
                self.aktywna_firma_stoper_kadry = firma_id
                self.czas_start_kadry = now
                conn.close()
                return

            # stop
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

            # switch
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

    def open_notes_window(self, firma_id: int):
        from app.monthly_view.monthly_view_ui import InlineNotesWindow
        InlineNotesWindow(firma_id).exec()

    def open_extra_service_window(self, firma_id: int):
        from app.monthly_view.monthly_view_ui import InlineExtraServiceWindow
        InlineExtraServiceWindow(firma_id, self.aktualny_miesiac).exec()

    def open_company_edit_window(self, firma_id: int):
        from app.companies.companies_ui import CompanyDialog
        dlg = CompanyDialog(self.pracownik_id, company_data=self._get_company_data(firma_id))
        if dlg.exec():
            self._save_company_data(firma_id, dlg.get_payload())
