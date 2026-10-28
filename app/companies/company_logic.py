# app/companies/company_logic.py
import psycopg
from typing import Any, Dict, Optional, List
from PyQt6.QtCore import QRunnable, QObject, pyqtSignal, QThreadPool, QDate

from app.companies.ceidg_api import get_ceidg_firma_by_nip
from app.companies.company_names import clean_company_name, sole_proprietor_short_name
from app.companies.address_parsing import split_street_address
from app.db.dao_conn import get_conn
from app.db.dao_companies import FIRMA_FIELDS
from app.db.dao_cenniki_kadry import get_kadry_price, get_kadry_description
import logging

logger = logging.getLogger(__name__)


# -------------------------
# Worker / Signals for CEIDG async fetch
# -------------------------
class WorkerSignals(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(Exception)
    finished = pyqtSignal()


class CEIDGWorker(QRunnable):
    def __init__(self, nip: str, form, fetch=None):
        super().__init__()
        self.nip = nip
        self.form = form
        self.fetch = fetch or get_ceidg_firma_by_nip
        self.signals = WorkerSignals()

    def run(self):
        try:
            res = self.fetch(self.nip)
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
        # Brak pola w odpowiedzi rejestru nie może wyczyścić wartości
        # wpisanej wcześniej przez użytkownika (np. adresu lub nazwy).
        if widget is None or value is None or value == "":
            return
        if hasattr(widget, "setText"):
            widget.setText(str(value or ""))
        elif hasattr(widget, "setDate") and isinstance(value, str):
            d = QDate.fromString(value[:10], "yyyy-MM-dd")
            if d.isValid():
                widget.setDate(d)
    except Exception:
        logger.warning("Zignorowany błąd", exc_info=True)


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
        _safe_set(form.inputs.get("nazwa"), clean_company_name(raw.get("nazwa")))

    wl = raw.get("wlasciciel") or {}
    short_name = sole_proprietor_short_name(
        raw.get("imie") or wl.get("imie") or wl.get("imiona"),
        raw.get("nazwisko") or wl.get("nazwisko"),
    )
    short_widget = form.inputs.get("nazwa_skrocona")
    if short_name and short_widget is not None:
        _safe_set(short_widget, short_name)

    # NIP / REGON
    if form.inputs.get("nip"):
        _safe_set(form.inputs.get("nip"), wl.get("nip") or raw.get("nip"))
    if form.inputs.get("regon"):
        _safe_set(form.inputs.get("regon"), wl.get("regon") or raw.get("regon"))

    if raw.get("krs") and form.inputs.get("krs") and not form.inputs["krs"].text().strip():
        _safe_set(form.inputs.get("krs"), raw.get("krs"))

    status_vat = form.inputs.get("status_vat")
    if raw.get("status_vat") and status_vat is not None and hasattr(status_vat, "setCurrentText"):
        status_vat.setCurrentText(raw["status_vat"])
    konto = form.inputs.get("konto_bankowe")
    if raw.get("konto_bankowe") and konto is not None and not konto.text().strip():
        _safe_set(konto, raw["konto_bankowe"])

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

    dz_ulica, dz_budynek, dz_lokal = split_street_address(
        raw.get("dz_ulica") or adres_dz.get("ulica") or adres_dz.get("ulic") or adres_dz.get("street"),
        raw.get("dz_budynek") or adres_dz.get("budynek") or adres_dz.get("building"),
        raw.get("dz_lokal") or adres_dz.get("lokal") or adres_dz.get("apartment"),
    )
    ko_ulica, ko_budynek, ko_lokal = split_street_address(
        raw.get("ko_ulica") or raw.get("koresp_ulica") or adres_ko.get("ulica") or adres_ko.get("ulic"),
        raw.get("ko_budynek") or raw.get("koresp_budynek") or adres_ko.get("budynek"),
        raw.get("ko_lokal") or raw.get("koresp_lokal") or adres_ko.get("lokal"),
    )

    if form.inputs.get("dz_miasto"):
        _safe_set(form.inputs.get("dz_miasto"),
                  raw.get("dz_miasto") or adres_dz.get("miasto") or raw.get("miasto"))
    if form.inputs.get("dz_kod"):
        _safe_set(form.inputs.get("dz_kod"),
                  raw.get("dz_kod") or adres_dz.get("kod") or adres_dz.get("kodPocztowy") or raw.get("kod"))
    if form.inputs.get("dz_ulica"):
        _safe_set(form.inputs.get("dz_ulica"), dz_ulica)
    if form.inputs.get("dz_budynek"):
        _safe_set(form.inputs.get("dz_budynek"), dz_budynek)
    if form.inputs.get("dz_lokal"):
        _safe_set(form.inputs.get("dz_lokal"), dz_lokal)
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
        _safe_set(form.inputs.get("ko_ulica"), ko_ulica)
    if form.inputs.get("ko_budynek"):
        _safe_set(form.inputs.get("ko_budynek"), ko_budynek)
    if form.inputs.get("ko_lokal"):
        _safe_set(form.inputs.get("ko_lokal"), ko_lokal)
    if form.inputs.get("ko_wojewodztwo"):
        _safe_set(form.inputs.get("ko_wojewodztwo"),
                  raw.get("ko_wojewodztwo") or raw.get("koresp_wojewodztwo") or adres_ko.get("wojewodztwo"))
    if form.inputs.get("ko_powiat"):
        _safe_set(form.inputs.get("ko_powiat"),
                  raw.get("ko_powiat") or raw.get("koresp_powiat") or adres_ko.get("powiat"))
    if form.inputs.get("ko_gmina"):
        _safe_set(form.inputs.get("ko_gmina"),
                  raw.get("ko_gmina") or raw.get("koresp_gmina") or adres_ko.get("gmina"))
    if form.inputs.get("ko_kraj"):
        _safe_set(form.inputs.get("ko_kraj"),
                  raw.get("ko_kraj") or raw.get("koresp_kraj") or adres_ko.get("kraj"))

    # kontakt
    if form.inputs.get("kont_imie"):
        _safe_set(
            form.inputs.get("kont_imie"),
            raw.get("imie") or raw.get("imiona") or wl.get("imie") or wl.get("imiona"),
        )
    if form.inputs.get("kont_nazwisko"):
        _safe_set(
            form.inputs.get("kont_nazwisko"),
            raw.get("nazwisko") or wl.get("nazwisko"),
        )
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


def ceidg_error(form, error: Exception, title: str = "CEIDG"):
    try:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(form, title, f"Błąd pobierania danych z {title}: {error}")
    except Exception:
        logger.warning("Zignorowany błąd", exc_info=True)


def ceidg_finished(form, button: str = "btn_ceidg", label: str = "Pobierz z CEIDG"):
    form._ceidg_in_progress = False
    try:
        btn = getattr(form, button, None)
        if btn is not None:
            btn.setEnabled(True)
            btn.setText(label)
    except Exception:
        logger.warning("Zignorowany błąd", exc_info=True)


def _apply_or_report(payload, title: str):
    form, data = payload
    if not data or data.get("error"):
        message = (data or {}).get("message") or "Nie znaleziono firmy."
        ceidg_error(form, Exception(message), title)
        return
    ceidg_apply(form, data)
    if data.get("_registry_warning"):
        try:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                form, title,
                "Pobrano dostępne informacje, ale nie wszystkie dane rejestrowe:\n"
                + str(data["_registry_warning"]),
            )
        except Exception:
            logger.warning("Nie można wyświetlić ostrzeżenia rejestrowego", exc_info=True)


def ceidg_fetch(form, fetch=None, button: str = "btn_ceidg",
                label: str = "Pobierz z CEIDG", title: str = "CEIDG",
                field: str = "nip", field_label: str = "NIP"):
    """
    Pobiera dane firmy po NIP w tle (domyślnie z CEIDG, opcjonalnie z innego źródła, np. GUS).
    Formularz musi mieć słownik inputs z polem 'nip' oraz opcjonalnie przycisk o nazwie `button`.
    """
    if getattr(form, "_ceidg_in_progress", False):
        return

    nip_widget = form.inputs.get(field)
    nip = "".join(ch for ch in (nip_widget.text() if nip_widget else "") if ch.isdigit())
    if len(nip) != 10:
        try:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(form, title, f"{field_label} musi składać się z 10 cyfr.")
        except Exception:
            logger.warning("Zignorowany błąd", exc_info=True)
        return

    form._ceidg_in_progress = True
    try:
        btn = getattr(form, button, None)
        if btn is not None:
            btn.setEnabled(False)
            btn.setText("Pobieranie...")
    except Exception:
        logger.warning("Zignorowany błąd", exc_info=True)

    worker = CEIDGWorker(nip, form, fetch)
    worker.signals.result.connect(lambda payload: _apply_or_report(payload, title))
    worker.signals.error.connect(lambda err: ceidg_error(form, err, title))
    # Utrzymujemy obiekt sygnałów/worker przez cały czas działania.
    # Bez referencji przy szybko kończących się zapytaniach callbacki
    # do formularza mogły zostać zwolnione przed obsługą zdarzeń GUI.
    form._ceidg_worker = worker
    def _done():
        ceidg_finished(form, button, label)
        form._ceidg_worker = None
    worker.signals.finished.connect(_done)
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
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM firmy
            WHERE id = %s
        """, (firma_id,))

        row = cur.fetchone()
        conn.close()

        return dict(row) if row else None

    # ---------------------------------------------------------
    # Pobieranie listy firm
    # ---------------------------------------------------------
    def get_all_companies(self) -> List[Dict[str, Any]]:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute("SELECT * FROM firmy")

        rows = cur.fetchall()
        conn.close()

        from app.utils.polish_sort import polish_sort_key

        return sorted((dict(r) for r in rows), key=lambda row: polish_sort_key(row.get("nazwa")))

    # ---------------------------------------------------------
    # Tworzenie nowej firmy
    # ---------------------------------------------------------
    def create_company(self, payload: Dict[str, Any]) -> int:
        fields = []
        values = []

        for key, val in payload.items():
            if key not in FIRMA_FIELDS:
                continue
            fields.append(key)
            values.append(val)
        if not fields:
            raise ValueError("Brak poprawnych pól firmy do zapisania.")

        conn = get_conn()
        cur = conn.cursor()

        cur.execute(f"""
            INSERT INTO firmy ({", ".join(fields)})
            VALUES ({", ".join(["%s"] * len(values))}) RETURNING id
        """, values)
        _new_id = cur.fetchone()[0]

        firma_id = _new_id
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
            if key not in FIRMA_FIELDS:
                continue
            fields.append(f"{key} = %s")
            values.append(val)
        if not fields:
            return

        values.append(firma_id)

        conn = get_conn()
        cur = conn.cursor()

        cur.execute(f"""
            UPDATE firmy
            SET {", ".join(fields)}
            WHERE id = %s
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
                uop_ilosc = %s,
                uz_ilosc = %s,
                uod_ilosc = %s,
                uop_cennik = %s,
                uz_cennik = %s,
                uod_cennik = %s
            WHERE id = %s
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

        cur.execute("DELETE FROM firmy WHERE id = %s", (firma_id,))

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
            WHERE firma_id = %s
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

        wanted = {int(p) for p in employee_ids}
        existing = {r[0] for r in cur.execute(
            "SELECT pracownik_id FROM pracownik_firma WHERE firma_id = %s", (firma_id,))}
        for pid in existing - wanted:
            cur.execute("DELETE FROM pracownik_firma WHERE firma_id = %s AND pracownik_id = %s", (firma_id, pid))
        for pid in wanted - existing:
            cur.execute("INSERT INTO pracownik_firma (pracownik_id, firma_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (pid, firma_id))

        conn.commit()
        conn.close()

    # ---------------------------------------------------------
    # Pobieranie opcji cennika kadrowego (użyteczne dla formularzy)
    # ---------------------------------------------------------
    def get_kadry_options(self) -> List[Dict[str, Any]]:
        conn = get_conn()
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
