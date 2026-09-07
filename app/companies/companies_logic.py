# app/companies/companies_logic.py

import sqlite3
from typing import List, Dict, Any

from PyQt6.QtCore import QRunnable, QObject, pyqtSignal, QThreadPool, QDate

from app.companies.ceidg_api import search_ceidg_by_nip
from app.db.dao_conn import get_conn


class WorkerSignals(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(Exception)
    finished = pyqtSignal()


class CEIDGWorker(QRunnable):
    def __init__(self, nip: str):
        super().__init__()
        self.nip = nip
        self.signals = WorkerSignals()

    def run(self):
        try:
            res = search_ceidg_by_nip(self.nip)
            self.signals.result.emit(res)
        except Exception as e:
            self.signals.error.emit(e)
        finally:
            self.signals.finished.emit()


def ceidg_fetch(form):
    if getattr(form, "_ceidg_in_progress", False):
        return

    nip = form.inputs["nip"].text().strip()
    if not nip:
        return

    form._ceidg_in_progress = True

    worker = CEIDGWorker(nip)
    worker.signals.result.connect(lambda data: ceidg_apply(form, data))
    worker.signals.error.connect(lambda err: ceidg_error(form, err))
    worker.signals.finished.connect(lambda: ceidg_finished(form))

    QThreadPool.globalInstance().start(worker)


def ceidg_apply(form, data: Dict[str, Any]):
    form._last_ceidg_raw = data or {}

    if not data or data.get("error"):
        return

    form.inputs["nazwa"].setText(data.get("nazwa", "") or "")
    form.inputs["nip"].setText(data.get("nip", "") or "")
    form.inputs["regon"].setText(data.get("regon", "") or "")
    form.inputs["kody_pkd"].setText(", ".join(data.get("pkd", []) or []))

    form.inputs["miasto"].setText(data.get("miasto", "") or "")
    form.inputs["kod_pocztowy"].setText(data.get("kod_pocztowy", "") or "")
    form.inputs["ulica"].setText(data.get("ulica", "") or "")
    form.inputs["adres_budynek"].setText(data.get("budynek", "") or "")

    ds = data.get("data_rozpoczecia")
    if ds:
        d = QDate.fromString(ds[:10], "yyyy-MM-dd")
        if d.isValid():
            form.inputs["data_rozpoczecia_dg"].setDate(d)


def ceidg_error(form, error: Exception):
    print("CEIDG error:", error)


def ceidg_finished(form):
    form._ceidg_in_progress = False


def get_ksiegowosc_options() -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT klucz,
               cena_netto_ksiegowosc AS netto,
               brutto,
               cena_za_dokument_ponad_limit AS ponad_limit
        FROM cenniki
        ORDER BY klucz
    """)

    rows = cur.fetchall()
    conn.close()

    result: List[Dict[str, Any]] = []
    for r in rows:
        result.append({
            "klucz": r["klucz"],
            "netto": float(r["netto"] or 0),
            "brutto": float(r["brutto"] or 0),
            "ponad_limit": float(r["ponad_limit"] or 0),
        })
    return result


def get_ksiegowosc_by_key(klucz: str) -> Dict[str, Any] | None:
    for item in get_ksiegowosc_options():
        if item["klucz"] == klucz:
            return item
    return None


def get_kadry_options() -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT klucz, cena_netto_kadry
        FROM cenniki_kadry
        ORDER BY klucz
    """)

    rows = cur.fetchall()
    conn.close()

    result: List[Dict[str, Any]] = []

    for r in rows:
        klucz = r["klucz"]
        cena = float(r["cena_netto_kadry"] or 0)

        if klucz.startswith("UOP"):
            nazwa = "Umowa o pracę"
        elif klucz.startswith("UZ"):
            nazwa = "Umowa Zlecenie"
        elif klucz.startswith("UOD"):
            nazwa = "Umowa o dzieło"
        else:
            nazwa = klucz

        result.append({
            "klucz": klucz,
            "typ": klucz.split("_")[0],
            "nazwa": nazwa,
            "cena": cena,
        })

    return result


def calculate_kadry_price(uop: int, uz: int, uod: int, selected_keys: List[str]) -> float:
    options = get_kadry_options()

    cena_uop = None
    cena_uz = None
    cena_uod = None

    for item in options:
        if item["klucz"] in selected_keys:
            if item["typ"] == "UOP":
                cena_uop = item["cena"]
            elif item["typ"] == "UZ":
                cena_uz = item["cena"]
            elif item["typ"] == "UOD":
                cena_uod = item["cena"]

    total = 0.0
    if cena_uop is not None:
        total += uop * cena_uop
    if cena_uz is not None:
        total += uz * cena_uz
    if cena_uod is not None:
        total += uod * cena_uod

    return total
