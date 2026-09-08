import sqlite3
from typing import Dict, Any, List

from PyQt6.QtCore import QRunnable, QObject, pyqtSignal, QThreadPool, QDate

from app.companies.ceidg_api import get_ceidg_firma_by_nip
from app.db.dao_conn import get_conn


class WorkerSignals(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(Exception)
    finished = pyqtSignal()


class CEIDGWorker(QRunnable):
    def __init__(self, nip: str, form):
        super().__init__()
        self.nip = nip
        self.form = form
        self.signals = WorkerSignals()

    def run(self):
        try:
            res = get_ceidg_firma_by_nip(self.nip)
            self.signals.result.emit((self.form, res))
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

    worker = CEIDGWorker(nip, form)
    worker.signals.result.connect(lambda payload: ceidg_apply(*payload))
    worker.signals.error.connect(lambda err: ceidg_error(form, err))
    worker.signals.finished.connect(lambda: ceidg_finished(form))

    QThreadPool.globalInstance().start(worker)


def ceidg_apply(form, data: Dict[str, Any]):
    form._last_ceidg_raw = data or {}

    if not data or data.get("error"):
        return

    # identyfikatory
    form.inputs["nazwa"].setText(data.get("nazwa", "") or "")
    form.inputs["nip"].setText(data.get("nip", "") or "")
    form.inputs["regon"].setText(data.get("regon", "") or "")
    form.inputs["kody_pkd"].setText(", ".join(data.get("pkd", []) or []))

    # adres działalności
    form.inputs["dz_miasto"].setText(data.get("dz_miasto", "") or "")
    form.inputs["dz_kod"].setText(data.get("dz_kod", "") or "")
    form.inputs["dz_ulica"].setText(data.get("dz_ulica", "") or "")
    form.inputs["dz_budynek"].setText(data.get("dz_budynek", "") or "")
    form.inputs["dz_lokal"].setText(data.get("dz_lokal", "") or "")
    form.inputs["dz_wojewodztwo"].setText(data.get("dz_wojewodztwo", "") or "")
    form.inputs["dz_powiat"].setText(data.get("dz_powiat", "") or "")
    form.inputs["dz_gmina"].setText(data.get("dz_gmina", "") or "")
    form.inputs["dz_kraj"].setText(data.get("dz_kraj", "") or "")

    # adres korespondencji
    form.inputs["ko_miasto"].setText(data.get("koresp_miasto", "") or "")
    form.inputs["ko_kod"].setText(data.get("koresp_kod", "") or "")
    form.inputs["ko_ulica"].setText(data.get("koresp_ulica", "") or "")
    form.inputs["ko_budynek"].setText(data.get("koresp_budynek", "") or "")
    form.inputs["ko_lokal"].setText(data.get("koresp_lokal", "") or "")
    form.inputs["ko_wojewodztwo"].setText(data.get("koresp_wojewodztwo", "") or "")
    form.inputs["ko_powiat"].setText(data.get("koresp_powiat", "") or "")
    form.inputs["ko_gmina"].setText(data.get("koresp_gmina", "") or "")
    form.inputs["ko_kraj"].setText(data.get("koresp_kraj", "") or "")

    # data rozpoczęcia DG
    ds = data.get("data_rozpoczecia")
    if ds:
        d = QDate.fromString(ds[:10], "yyyy-MM-dd")
        if d.isValid():
            form.inputs["data_rozpoczecia_dg"].setDate(d)


def ceidg_error(form, error: Exception):
    print("CEIDG error:", error)


def ceidg_finished(form):
    form._ceidg_in_progress = False


# cenniki księgowe
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


# cenniki kadrowe
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
