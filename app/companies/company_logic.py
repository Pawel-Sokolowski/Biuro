# app/companies/company_logic.py
import sqlite3
from typing import Any, Dict, Optional, List
from PyQt6.QtCore import QRunnable, QObject, pyqtSignal, QThreadPool, QDate

from app.companies.ceidg_api import get_ceidg_firma_by_nip
from app.db.dao_conn import get_conn
from app.db.dao_cenniki_kadry import get_kadry_price, get_kadry_description


# -------------------------
# Worker / Signals for CEIDG async fetch
# -------------------------
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


# -------------------------
# CEIDG helpers (UI integration helpers)
# -------------------------
def _safe_set(widget, value):
    try:
        if widget is None:
            return
        if hasattr(widget, "setText"):
            widget.setText(str(value or ""))
        elif hasattr(widget, "setDate") and isinstance(value, str):
            d = QDate.fromString(value[:10], "yyyy-MM-dd")
            if d.isValid():
                widget.setDate(d)
    except Exception:
        pass


def ceidg_apply(form, data: Optional[Dict[str, Any]]):
    """
    Apply CEIDG response to form widgets. Form is expected to have `inputs` dict mapping keys to widgets.
    """
    form._last_ceidg_raw = data or {}
    if not data or data.get("error"):
        return

    if isinstance(data, dict) and "firma" in data and data["firma"]:
        raw = data["firma"][0]
    else:
        raw = data

    # nazwa
    if form.inputs.get("nazwa"):
        _safe_set(form.inputs.get("nazwa"), raw.get("nazwa"))

    wl = raw.get("wlasciciel") or {}

    # NIP / REGON
    if form.inputs.get("nip"):
        _safe_set(form.inputs.get("nip"), wl.get("nip") or raw.get("nip"))
    if form.inputs.get("regon"):
        _safe_set(form.inputs.get("regon"), wl.get("regon") or raw.get("regon"))

    # PKD
    pkd_list = raw.get("pkd") or raw.get("pkdList") or []
    pkd_codes: List[str] = []
    if isinstance(pkd_list, list):
        for p in pkd_list:
            if isinstance(p, dict):
                code = p.get("kod") or p.get("code")
                if code:
                    pkd_codes.append(code)
            elif isinstance(p, str):
                pkd_codes.append(p)
    elif isinstance(pkd_list, str):
        pkd_codes = [pkd_list]

    main_pkd = raw.get("pkdGlowny") or raw.get("pkd_glowny") or {}
    if isinstance(main_pkd, dict):
        mp = main_pkd.get("kod") or main_pkd.get("code")
        if mp and mp not in pkd_codes:
            pkd_codes.insert(0, mp)

    if form.inputs.get("kody_pkd"):
        _safe_set(form.inputs.get("kody_pkd"), ", ".join(pkd_codes))

    # data rozpoczęcia DG
    ds = raw.get("dataRozpoczecia") or raw.get("data_rozpoczecia") or raw.get("dataRozpoczeciaDG")
    if ds and form.inputs.get("data_rozpoczecia_dg"):
        _safe_set(form.inputs.get("data_rozpoczecia_dg"), ds)

    # adresy
    adres_dz = raw.get("adresDzialalnosci") or raw.get("adres_dzialalnosci") or {}
    adres_ko = raw.get("adresKorespondencyjny") or raw.get("adres_korespondencyjny") or {}

    if form.inputs.get("dz_miasto"):
        _safe_set(form.inputs.get("dz_miasto"),
                  raw.get("dz_miasto") or adres_dz.get("miasto") or raw.get("miasto"))
    if form.inputs.get("dz_kod"):
        _safe_set(form.inputs.get("dz_kod"),
                  raw.get("dz_kod") or adres_dz.get("kod") or adres_dz.get("kodPocztowy") or raw.get("kod"))
    if form.inputs.get("dz_ulica"):
        _safe_set(form.inputs.get("dz_ulica"),
                  raw.get("dz_ulica") or adres_dz.get("ulica") or adres_dz.get("ulic") or adres_dz.get("street"))
    if form.inputs.get("dz_budynek"):
        _safe_set(form.inputs.get("dz_budynek"),
                  raw.get("dz_budynek") or adres_dz.get("budynek") or adres_dz.get("building"))
    if form.inputs.get("dz_lokal"):
        _safe_set(form.inputs.get("dz_lokal"),
                  raw.get("dz_lokal") or adres_dz.get("lokal") or adres_dz.get("apartment"))
    if form.inputs.get("dz_wojewodztwo"):
        _safe_set(form.inputs.get("dz_wojewodztwo"),
                  raw.get("dz_wojewodztwo") or adres_dz.get("wojewodztwo") or adres_dz.get("province"))
    if form.inputs.get("dz_powiat"):
        _safe_set(form.inputs.get("dz_powiat"),
                  raw.get("dz_powiat") or adres_dz.get("powiat") or adres_dz.get("county"))
    if form.inputs.get("dz_gmina"):
        _safe_set(form.inputs.get("dz_gmina"),
                  raw.get("dz_gmina") or adres_dz.get("gmina") or adres_dz.get("commune"))
    if form.inputs.get("dz_kraj"):
        _safe_set(form.inputs.get("dz_kraj"),
                  raw.get("dz_kraj") or adres_dz.get("kraj") or adres_dz.get("country"))

    if form.inputs.get("ko_miasto"):
        _safe_set(form.inputs.get("ko_miasto"),
                  raw.get("ko_miasto") or adres_ko.get("miasto") or raw.get("koresp_miasto"))
    if form.inputs.get("ko_kod"):
        _safe_set(form.inputs.get("ko_kod"),
                  raw.get("ko_kod") or adres_ko.get("kod") or adres_ko.get("kodPocztowy") or raw.get("koresp_kod"))
    if form.inputs.get("ko_ulica"):
        _safe_set(form.inputs.get("ko_ulica"),
                  raw.get("ko_ulica") or adres_ko.get("ulica") or adres_ko.get("ulic") or raw.get("koresp_ulica"))
    if form.inputs.get("ko_budynek"):
        _safe_set(form.inputs.get("ko_budynek"),
                  raw.get("ko_budynek") or adres_ko.get("budynek"))
    if form.inputs.get("ko_lokal"):
        _safe_set(form.inputs.get("ko_lokal"),
                  raw.get("ko_lokal") or adres_ko.get("lokal"))
    if form.inputs.get("ko_wojewodztwo"):
        _safe_set(form.inputs.get("ko_wojewodztwo"),
                  raw.get("ko_wojewodztwo") or adres_ko.get("wojewodztwo"))
    if form.inputs.get("ko_powiat"):
        _safe_set(form.inputs.get("ko_powiat"),
                  raw.get("ko_powiat") or adres_ko.get("powiat"))
    if form.inputs.get("ko_gmina"):
        _safe_set(form.inputs.get("ko_gmina"),
                  raw.get("ko_gmina") or adres_ko.get("gmina"))
    if form.inputs.get("ko_kraj"):
        _safe_set(form.inputs.get("ko_kraj"),
                  raw.get("ko_kraj") or adres_ko.get("kraj"))

    # kontakt
    if form.inputs.get("kont_imie"):
        _safe_set(form.inputs.get("kont_imie"), wl.get("imie"))
    if form.inputs.get("kont_nazw"):
        _safe_set(form.inputs.get("kont_nazw"), wl.get("nazwisko"))
    if form.inputs.get("kont_email"):
        _safe_set(form.inputs.get("kont_email"), raw.get("email") or raw.get("kontaktEmail"))
    if form.inputs.get("kont_tel"):
        _safe_set(form.inputs.get("kont_tel"), raw.get("telefon") or raw.get("kontaktTelefon"))

    # e-doręczenia
    if form.inputs.get("adres_doreczen_elektronicznych"):
        _safe_set(
            form.inputs.get("adres_doreczen_elektronicznych"),
            raw.get("adresDoreczenElektronicznych")
            or raw.get("adres_doreczen_elektronicznych")
            or raw.get("adres_doreczen")
        )

    return


def ceidg_error(form, error: Exception):
    try:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(form, "CEIDG", f"Błąd pobierania danych z CEIDG: {error}")
    except Exception:
        pass


def ceidg_finished(form):
    form._ceidg_in_progress = False
    try:
        if hasattr(form, "btn_ceidg"):
            form.btn_ceidg.setEnabled(True)
            form.btn_ceidg.setText("Pobierz z CEIDG")
    except Exception:
        pass


def ceidg_fetch(form):
    """
    Kick off CEIDG fetch in background. Form must have inputs dict with 'nip' widget and optional btn_ceidg.
    """
    if getattr(form, "_ceidg_in_progress", False):
        return

    nip_widget = form.inputs.get("nip")
    nip = nip_widget.text().strip() if nip_widget else ""
    if not nip:
        try:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(form, "CEIDG", "Podaj NIP przed pobraniem danych z CEIDG.")
        except Exception:
            pass
        return

    form._ceidg_in_progress = True
    try:
        if hasattr(form, "btn_ceidg"):
            form.btn_ceidg.setEnabled(False)
            form.btn_ceidg.setText("Pobieranie...")
    except Exception:
        pass

    worker = CEIDGWorker(nip, form)
    worker.signals.result.connect(lambda payload: ceidg_apply(*payload))
    worker.signals.error.connect(lambda err: ceidg_error(form, err))
    worker.signals.finished.connect(lambda: ceidg_finished(form))
    QThreadPool.globalInstance().start(worker)


# -------------------------
# Cennik helpers
# -------------------------
def build_cennik_key(forma: str, vat_czynny: bool, limit: int) -> str:
    """
    Buduje sugerowany klucz cennika na podstawie:
      - forma: "KPiR", "Ryczałt", "Księgi rachunkowe"
      - vat_czynny: True/False
      - limit: liczba dokumentów (ręczna)
    Przykłady:
      KPiR, vat True, limit 50 -> "KPIR_T_50"
      Ryczałt, vat False, limit 10 -> "RYCZALT_N_10"
      Księgi rachunkowe -> "KR_T_..."
    """
    if forma == "KPiR":
        prefix = "KPIR"
    elif forma == "Ryczałt":
        prefix = "RYCZALT"
    else:
        prefix = "KR"

    vat_part = "T" if vat_czynny else "N"

    try:
        lim = int(limit)
    except Exception:
        lim = 0

    if lim > 0:
        return f"{prefix}_{vat_part}_{lim}"
    return f"{prefix}_{vat_part}"


def _extract_limit_from_key(klucz: str) -> Optional[int]:
    """
    Wyciąga liczbę limitu z klucza, np. KPIR_T_50 -> 50
    """
    if not klucz:
        return None
    parts = klucz.split("_")
    for p in reversed(parts):
        try:
            return int(p)
        except Exception:
            continue
    return None


def pick_cennik_by_key_or_limit(desired_key: str, limit: int, cenniki_rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Wybiera najlepszy cennik:
      1) jeśli istnieje dokładny klucz desired_key -> zwróć go
      2) w przeciwnym razie znajdź cenniki o tym samym prefiksie i vat_part,
         wybierz największy limit <= limit; jeśli brak, wybierz najmniejszy limit >= limit (zaokrąglenie w górę)
      3) fallback: pierwszy pasujący prefiks
    """
    if not cenniki_rows:
        return None

    # exact match
    for r in cenniki_rows:
        if r.get("klucz") == desired_key:
            return r

    # parse desired prefix and vat part
    parts = desired_key.split("_")
    prefix = parts[0] if parts else desired_key
    vat_part = parts[1] if len(parts) >= 2 else None

    candidates = []
    for r in cenniki_rows:
        k = r.get("klucz", "")
        kp = k.split("_")
        if not kp:
            continue
        if kp[0] != prefix:
            continue
        if vat_part and len(kp) >= 2 and kp[1] != vat_part:
            continue
        candidates.append(r)

    if not candidates:
        return None

    # collect limits
    limits = []
    for c in candidates:
        lim = _extract_limit_from_key(c.get("klucz", ""))
        if lim is not None:
            limits.append((lim, c))
    # sort by limit ascending
    limits.sort(key=lambda x: x[0])

    # try largest <= limit
    chosen = None
    for lim, row in reversed(limits):
        if lim <= limit:
            chosen = row
            break

    if chosen:
        return chosen

    # jeśli nie znaleziono <= limit, wybierz najmniejszy >= limit (zaokrąglenie w górę)
    for lim, row in limits:
        if lim >= limit:
            return row

    # fallback: jeśli żaden limit nie istnieje, zwróć pierwszy candidate
    if not limits:
        return candidates[0]

    # final fallback
    return limits[-1][1] if limits else candidates[0]


# -------------------------
# CompanyLogic core (rozszerzona wersja z oryginalnego pliku)
# -------------------------
class CompanyLogic:
    def __init__(self):
        pass

    # ---------------------------------------------------------
    # Pobieranie firmy
    # ---------------------------------------------------------
    def get_company(self, firma_id: int) -> Optional[Dict[str, Any]]:
        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM firmy
            WHERE id = ?
        """, (firma_id,))

        row = cur.fetchone()
        conn.close()

        return dict(row) if row else None

    # ---------------------------------------------------------
    # Pobieranie listy firm
    # ---------------------------------------------------------
    def get_all_companies(self) -> List[Dict[str, Any]]:
        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("SELECT * FROM firmy ORDER BY nazwa")

        rows = cur.fetchall()
        conn.close()

        return [dict(r) for r in rows]

    # ---------------------------------------------------------
    # Tworzenie nowej firmy
    # ---------------------------------------------------------
    def create_company(self, payload: Dict[str, Any]) -> int:
        fields = []
        values = []

        for key, val in payload.items():
            fields.append(key)
            values.append(val)

        conn = get_conn()
        cur = conn.cursor()

        cur.execute(f"""
            INSERT INTO firmy ({", ".join(fields)})
            VALUES ({", ".join(["?"] * len(values))})
        """, values)

        firma_id = cur.lastrowid
        conn.commit()
        conn.close()

        return firma_id

    # ---------------------------------------------------------
    # Zapisywanie firmy (pełny update)
    # ---------------------------------------------------------
    def save_company(self, firma_id: int, payload: Dict[str, Any]) -> None:
        fields = []
        values = []

        for key, val in payload.items():
            fields.append(f"{key} = ?")
            values.append(val)

        values.append(firma_id)

        conn = get_conn()
        cur = conn.cursor()

        cur.execute(f"""
            UPDATE firmy
            SET {", ".join(fields)}
            WHERE id = ?
        """, values)

        conn.commit()
        conn.close()

    # ---------------------------------------------------------
    # Kadry — pobieranie cen z cennika
    # ---------------------------------------------------------
    def calculate_kadry_values(self, firma: Dict[str, Any]) -> Dict[str, Any]:
        """
        Zwraca:
        {
            "uop": {"ilosc": X, "cennik": "...", "cena": Y, "netto": Z, "brutto": W, "opis": "..."},
            "uz": {...},
            "uod": {...}
        }
        """

        result = {}

        # -------------------------
        # UoP
        # -------------------------
        uop_ilosc = firma.get("uop_ilosc") or 0
        uop_key = firma.get("uop_cennik")
        uop_cena = get_kadry_price(uop_key) if uop_key else 0
        uop_opis = get_kadry_description(uop_key) if uop_key else ""

        uop_netto = round(uop_ilosc * uop_cena, 2)
        uop_brutto = round(uop_netto * 1.23, 2)

        result["uop"] = {
            "ilosc": uop_ilosc,
            "cennik": uop_key,
            "cena": uop_cena,
            "netto": uop_netto,
            "brutto": uop_brutto,
            "opis": uop_opis,
        }

        # -------------------------
        # UZ
        # -------------------------
        uz_ilosc = firma.get("uz_ilosc") or 0
        uz_key = firma.get("uz_cennik")
        uz_cena = get_kadry_price(uz_key) if uz_key else 0
        uz_opis = get_kadry_description(uz_key) if uz_key else ""

        uz_netto = round(uz_ilosc * uz_cena, 2)
        uz_brutto = round(uz_netto * 1.23, 2)

        result["uz"] = {
            "ilosc": uz_ilosc,
            "cennik": uz_key,
            "cena": uz_cena,
            "netto": uz_netto,
            "brutto": uz_brutto,
            "opis": uz_opis,
        }

        # -------------------------
        # UoD
        # -------------------------
        uod_ilosc = firma.get("uod_ilosc") or 0
        uod_key = firma.get("uod_cennik")
        uod_cena = get_kadry_price(uod_key) if uod_key else 0
        uod_opis = get_kadry_description(uod_key) if uod_key else ""

        uod_netto = round(uod_ilosc * uod_cena, 2)
        uod_brutto = round(uod_netto * 1.23, 2)

        result["uod"] = {
            "ilosc": uod_ilosc,
            "cennik": uod_key,
            "cena": uod_cena,
            "netto": uod_netto,
            "brutto": uod_brutto,
            "opis": uod_opis,
        }

        return result

    # ---------------------------------------------------------
    # Kadry — zapis tylko sekcji kadrowej
    # ---------------------------------------------------------
    def save_kadry_section(self, firma_id: int, uop_ilosc: int, uz_ilosc: int, uod_ilosc: int,
                           uop_cennik: Optional[str], uz_cennik: Optional[str], uod_cennik: Optional[str]) -> None:

        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
            UPDATE firmy
            SET
                uop_ilosc = ?,
                uz_ilosc = ?,
                uod_ilosc = ?,
                uop_cennik = ?,
                uz_cennik = ?,
                uod_cennik = ?
            WHERE id = ?
        """, (
            uop_ilosc,
            uz_ilosc,
            uod_ilosc,
            uop_cennik,
            uz_cennik,
            uod_cennik,
            firma_id
        ))

        conn.commit()
        conn.close()

    # ---------------------------------------------------------
    # Usuwanie firmy
    # ---------------------------------------------------------
    def delete_company(self, firma_id: int) -> None:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute("DELETE FROM firmy WHERE id = ?", (firma_id,))

        conn.commit()
        conn.close()

    # ---------------------------------------------------------
    # Pobieranie przypisanych pracowników
    # ---------------------------------------------------------
    def get_assigned_employees(self, firma_id: int) -> List[int]:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
            SELECT pracownik_id
            FROM pracownik_firma
            WHERE firma_id = ?
        """, (firma_id,))

        rows = cur.fetchall()
        conn.close()

        return [r[0] for r in rows]

    # ---------------------------------------------------------
    # Przypisywanie pracowników
    # ---------------------------------------------------------
    def assign_employees(self, firma_id: int, employee_ids: List[int]) -> None:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute("DELETE FROM pracownik_firma WHERE firma_id = ?", (firma_id,))

        for pid in employee_ids:
            cur.execute("""
                INSERT INTO pracownik_firma (pracownik_id, firma_id)
                VALUES (?, ?)
            """, (pid, firma_id))

        conn.commit()
        conn.close()

    # ---------------------------------------------------------
    # Pobieranie opcji cennika kadrowego (użyteczne dla formularzy)
    # ---------------------------------------------------------
    def get_kadry_options(self) -> List[Dict[str, Any]]:
        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT klucz, cena, opis
            FROM cenniki_kadry
            ORDER BY klucz
        """)
        rows = cur.fetchall()
        conn.close()
        result: List[Dict[str, Any]] = []
        for r in rows:
            klucz = r["klucz"]
            cena = float(r["cena"] or 0)
            if klucz.upper().startswith("UOP"):
                nazwa = "Umowa o pracę"
            elif klucz.upper().startswith("UZ"):
                nazwa = "Umowa zlecenie"
            elif klucz.upper().startswith("UOD"):
                nazwa = "Umowa o dzieło"
            else:
                nazwa = klucz
            result.append({
                "klucz": klucz,
                "typ": klucz.split("_")[0] if "_" in klucz else klucz,
                "nazwa": nazwa,
                "cena": cena,
                "opis": r.get("opis", "")
            })
        return result

    # ---------------------------------------------------------
    # Kalkulator kadrowy pomocniczy (suma netto)
    # ---------------------------------------------------------
    def calculate_kadry_price(self, uop: int, uz: int, uod: int, selected_keys: List[str]) -> float:
        options = self.get_kadry_options()
        cena_uop = None
        cena_uz = None
        cena_uod = None
        for item in options:
            if item["klucz"] in selected_keys:
                typ = item.get("typ", "").upper()
                if typ == "UOP":
                    cena_uop = item["cena"]
                elif typ == "UZ":
                    cena_uz = item["cena"]
                elif typ == "UOD":
                    cena_uod = item["cena"]
        total = 0.0
        if cena_uop is not None:
            total += uop * cena_uop
        if cena_uz is not None:
            total += uz * cena_uz
        if cena_uod is not None:
            total += uod * cena_uod
        return round(total, 2)
