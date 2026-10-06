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
                   COALESCE(sm.ilosc_dok, 0) AS ilosc_dok,
                   COALESCE(sm.sekundy_ksiegowosc, 0) AS sekundy_ksiegowosc,
                   COALESCE(sm.sekundy_kadry, 0) AS sekundy_kadry
            FROM firmy f
            LEFT JOIN statusy_miesieczne sm
                ON sm.firma_id = f.id AND sm.miesiac = ?
            ORDER BY f.nazwa
        """,
            (selected_month,),
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

    def toggle_stoper(
        self, firma_id: int, typ: str, activity_payload: Optional[Dict[str, Any]] = None
    ):
        now = datetime.datetime.now()
        self._ensure_month_status(firma_id)

        conn = get_conn()
        cur = conn.cursor()

        if typ == "ksiegowosc":
            if self.aktywna_firma_stoper_ks is None:
                self.aktywna_firma_stoper_ks = firma_id
                self.czas_start_ks = now
                self.aktywna_firma_stoper_ks_activity = activity_payload
                conn.close()
                return

            if self.aktywna_firma_stoper_ks == firma_id:
                delta = int((now - self.czas_start_ks).total_seconds())
                cur.execute(
                    """
                    UPDATE statusy_miesieczne
                    SET sekundy_ksiegowosc = sekundy_ksiegowosc + ?
                    WHERE firma_id = ? AND miesiac = ?
                """,
                    (delta, firma_id, self.aktualny_miesiac),
                )
                conn.commit()
                conn.close()

                act = self.aktywna_firma_stoper_ks_activity
                if act and act.get("typ_czynnosci") == "extra" and act.get("fakturowac"):
                    hours = TimerUtils.round_seconds_to_minimum_half(delta)
                    add_invoice_item_for_service(
                        firma_id,
                        act["usluga_id"],
                        hours,
                        self.aktualny_miesiac,
                        self.pracownik_id,
                    )

                self.aktywna_firma_stoper_ks = None
                self.czas_start_ks = None
                self.aktywna_firma_stoper_ks_activity = None
                return

            prev = self.aktywna_firma_stoper_ks
            delta_prev = int((now - self.czas_start_ks).total_seconds())
            cur.execute(
                """
                UPDATE statusy_miesieczne
                SET sekundy_ksiegowosc = sekundy_ksiegowosc + ?
                WHERE firma_id = ? AND miesiac = ?
            """,
                (delta_prev, prev, self.aktualny_miesiac),
            )

            prev_act = self.aktywna_firma_stoper_ks_activity
            if prev_act and prev_act.get("typ_czynnosci") == "extra" and prev_act.get("fakturowac"):
                hours_prev = TimerUtils.round_seconds_to_minimum_half(delta_prev)
                add_invoice_item_for_service(
                    prev,
                    prev_act["usluga_id"],
                    hours_prev,
                    self.aktualny_miesiac,
                    self.pracownik_id,
                )

            self.aktywna_firma_stoper_ks = firma_id
            self.czas_start_ks = now
            self.aktywna_firma_stoper_ks_activity = activity_payload
            conn.commit()
            conn.close()
            return

        if typ == "kadry":
            if self.aktywna_firma_stoper_kadry is None:
                self.aktywna_firma_stoper_kadry = firma_id
                self.czas_start_kadry = now
                self.aktywna_firma_stoper_kadry_activity = activity_payload
                conn.close()
                return

            if self.aktywna_firma_stoper_kadry == firma_id:
                delta = int((now - self.czas_start_kadry).total_seconds())
                cur.execute(
                    """
                    UPDATE statusy_miesieczne
                    SET sekundy_kadry = sekundy_kadry + ?
                    WHERE firma_id = ? AND miesiac = ?
                """,
                    (delta, firma_id, self.aktualny_miesiac),
                )
                conn.commit()
                conn.close()

                act = self.aktywna_firma_stoper_kadry_activity
                if act and act.get("typ_czynnosci") == "extra" and act.get("fakturowac"):
                    hours = TimerUtils.round_seconds_to_minimum_half(delta)
                    add_invoice_item_for_service(
                        firma_id,
                        act["usluga_id"],
                        hours,
                        self.aktualny_miesiac,
                        self.pracownik_id,
                    )

                self.aktywna_firma_stoper_kadry = None
                self.czas_start_kadry = None
                self.aktywna_firma_stoper_kadry_activity = None
                return

            prev = self.aktywna_firma_stoper_kadry
            delta_prev = int((now - self.czas_start_kadry).total_seconds())
            cur.execute(
                """
                UPDATE statusy_miesieczne
                SET sekundy_kadry = sekundy_kadry + ?
                WHERE firma_id = ? AND miesiac = ?
            """,
                (delta_prev, prev, self.aktualny_miesiac),
            )

            prev_act = self.aktywna_firma_stoper_kadry_activity
            if prev_act and prev_act.get("typ_czynnosci") == "extra" and prev_act.get("fakturowac"):
                hours_prev = TimerUtils.round_seconds_to_minimum_half(delta_prev)
                add_invoice_item_for_service(
                    prev,
                    prev_act["usluga_id"],
                    hours_prev,
                    self.aktualny_miesiac,
                    self.pracownik_id,
                )

            self.aktywna_firma_stoper_kadry = firma_id
            self.czas_start_kadry = now
            self.aktywna_firma_stoper_kadry_activity = activity_payload
            conn.commit()
            conn.close()
            return

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
        from PyQt6.QtWidgets import QDialog, QVBoxLayout
        # import CompanyForm lazily
        from app.companies.company_form import CompanyForm

        company_data = self._get_company_data(firma_id)

        dialog = QDialog()
        dialog.setWindowTitle("Edycja firmy")
        dialog.setModal(True)
        dialog.resize(700, 600)

        layout = QVBoxLayout(dialog)

        form = None
        # Spróbuj kilku wariantów wywołania konstruktora, bez przerywania aplikacji
        try:
            # 1) najpierw spróbuj standardowo: (pracownik_id, company_data, parent)
            form = CompanyForm(self.pracownik_id, company_data, dialog)
        except TypeError:
            try:
                # 2) spróbuj (pracownik_id, parent, company_data) - niektóre wersje mają parent jako drugi arg
                form = CompanyForm(self.pracownik_id, dialog, company_data)
            except TypeError:
                try:
                    # 3) spróbuj z keywordami (jeśli dostępne)
                    form = CompanyForm(self.pracownik_id, parent=dialog)
                    # ustaw dane ręcznie, jeśli formularz ma odpowiednią metodę/atrybut
                    if hasattr(form, "load_company_data"):
                        form.load_company_data(company_data)
                    elif hasattr(form, "set_company_data"):
                        form.set_company_data(company_data)
                    elif hasattr(form, "company_data"):
                        try:
                            form.company_data = company_data
                        except Exception:
                            pass
                except Exception:
                    # ostateczny fallback: spróbuj tylko z pracownik_id
                    try:
                        form = CompanyForm(self.pracownik_id)
                        if hasattr(form, "load_company_data"):
                            form.load_company_data(company_data)
                        elif hasattr(form, "set_company_data"):
                            form.set_company_data(company_data)
                        elif hasattr(form, "company_data"):
                            try:
                                form.company_data = company_data
                            except Exception:
                                pass
                    except Exception as e:
                        # jeśli wszystko zawiedzie, pokaż błąd w konsoli i zamknij dialog
                        print("ERROR: Nie udało się utworzyć CompanyForm:", e)
                        dialog.reject()
                        return

        def _on_saved():
            dialog.accept()

        # jeśli form został utworzony, podłącz i pokaż
        if form is not None:
            try:
                form.on_saved = _on_saved
            except Exception:
                # jeśli form nie ma on_saved, ignorujemy
                pass
            layout.addWidget(form)
            dialog.exec()

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
