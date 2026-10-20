"""Cykliczne sprawdzanie w CEIDG, czy działalność firmy jest zawieszona."""
from __future__ import annotations

import logging
import threading
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from app.db.dao_app_settings import get_setting, set_setting
from app.db.dao_conn import get_conn
from app.db.dao_tasks import notify_company_resumed, notify_company_suspended

logger = logging.getLogger(__name__)

LAST_RUN_KEY = "ceidg_ostatnie_sprawdzenie_zawieszen"
SUSPENDED_STATUSES = {"ZAWIESZONY", "ZAWIESZONA"}
MAX_GAP_DAYS = 15

_lock = threading.Lock()


def _last_day(d: date) -> int:
    nxt = d.replace(day=28) + timedelta(days=4)
    return (nxt - timedelta(days=nxt.day)).day


def is_check_due(today: Optional[date] = None) -> bool:
    """Sprawdzamy 1. i ostatniego dnia miesiąca oraz nadrabiamy zaległości,
    jeśli program nie był uruchomiony w tych dniach."""
    today = today or date.today()
    raw = get_setting(LAST_RUN_KEY)
    last: Optional[date] = None
    if raw:
        try:
            last = date.fromisoformat(raw)
        except ValueError:
            last = None
    if last == today:
        return False
    if last is None or (today - last).days > MAX_GAP_DAYS:
        return True
    return today.day in (1, _last_day(today))


def _candidates() -> List[Dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, nip, COALESCE(status, 'Aktywne'), COALESCE(ceidg_zawieszona, 0) FROM firmy "
            "WHERE COALESCE(status, 'Aktywne') != 'Archiwalne' AND COALESCE(TRIM(nip), '') != ''"
        ).fetchall()
    finally:
        conn.close()
    return [{"id": r[0], "nip": r[1], "status": r[2], "flag": int(r[3])} for r in rows]


def _apply(firma_id: int, flag: int, status: Optional[str] = None, since: Optional[str] = None) -> None:
    sets, params = ["ceidg_zawieszona = %s"], [flag]
    if status is not None:
        sets.append("status = %s")
        params.append(status)
    if since is not None:
        sets.append("zawieszenia_dzialalnosci = %s")
        params.append(since)
    conn = get_conn()
    try:
        conn.execute(f"UPDATE firmy SET {', '.join(sets)} WHERE id = %s", params + [firma_id])
        conn.commit()
    finally:
        conn.close()


def _process(firm: Dict, data: dict) -> Optional[str]:
    """Aktualizuje firmę wg odpowiedzi CEIDG. Zwraca "suspended"/"resumed"/None."""
    is_suspended = str(data["status"]).upper() in SUSPENDED_STATUSES
    if is_suspended and not firm["flag"]:
        since = data.get("data_zawieszenia") or date.today().isoformat()
        moved = firm["status"] == "Aktywne"
        _apply(firm["id"], 1, "Zawieszone" if moved else None, since)
        if moved:
            notify_company_suspended(firm["id"])
        return "suspended"
    if not is_suspended and firm["flag"]:
        _apply(firm["id"], 0, "Aktywne" if firm["status"] == "Zawieszone" else None)
        notify_company_resumed(firm["id"])
        return "resumed"
    return None


def check_ceidg_state(fetch: Optional[Callable[[str], dict]] = None) -> Tuple[List[int], List[int]]:
    """Porównuje stan działalności w CEIDG z zapamiętanym w firmie (ceidg_zawieszona).
    Zawieszenie działalności przenosi firmę do „Zawieszone”, odwieszenie ją przywraca;
    oba zdarzenia tworzą zadania-powiadomienia. Zawieszenie usług (uslugi_zawieszone)
    jest niezależne i tu się go nie ruszą. Firmy zawieszone ręcznie nie wracają same.
    Zwraca (id zawieszonych, id odwieszonych)."""
    if fetch is None:
        from app.companies.ceidg_api import get_ceidg_firma_by_nip as fetch

    suspended: List[int] = []
    resumed: List[int] = []
    for firm in _candidates():
        nip = "".join(ch for ch in str(firm["nip"]) if ch.isdigit())
        if len(nip) != 10:
            continue
        try:
            data = fetch(nip)
        except Exception:
            logger.warning("CEIDG: błąd zapytania dla firmy %s", firm["id"], exc_info=True)
            continue
        if not data or data.get("error") or not data.get("status"):
            continue
        outcome = _process(firm, data)
        if outcome == "suspended":
            suspended.append(firm["id"])
        elif outcome == "resumed":
            resumed.append(firm["id"])
    return suspended, resumed


def check_company_ceidg(firma_id: int, fetch: Optional[Callable[[str], dict]] = None) -> Dict:
    """Ręczne sprawdzenie jednej firmy. Zwraca {"state": "zawieszona"|"aktywna"|None,
    "change": "suspended"|"resumed"|None, "error": komunikat|None}."""
    if fetch is None:
        import os
        from app.utils.env_config import load_environment
        from app.companies.ceidg_api import get_ceidg_firma_by_nip as fetch
        load_environment()
        if not (os.getenv("CEIDG_API_KEY") and os.getenv("CEIDG_BASE_URL")):
            return {"state": None, "change": None, "error": "Brak konfiguracji CEIDG (CEIDG_API_KEY / CEIDG_BASE_URL)."}
    firm = next((f for f in _candidates() if f["id"] == firma_id), None)
    if not firm:
        return {"state": None, "change": None, "error": "Firma jest archiwalna albo nie ma NIP-u."}
    nip = "".join(ch for ch in str(firm["nip"]) if ch.isdigit())
    if len(nip) != 10:
        return {"state": None, "change": None, "error": "Firma nie ma poprawnego NIP-u."}
    try:
        data = fetch(nip)
    except Exception as exc:
        return {"state": None, "change": None, "error": f"Błąd zapytania do CEIDG: {exc}"}
    if not data or data.get("error") or not data.get("status"):
        msg = (data or {}).get("message") or "CEIDG nie zwrócił statusu firmy."
        return {"state": None, "change": None, "error": msg}
    change = _process(firm, data)
    state = "zawieszona" if str(data["status"]).upper() in SUSPENDED_STATUSES else "aktywna"
    return {"state": state, "change": change, "error": None}


def check_suspensions(fetch: Optional[Callable[[str], dict]] = None) -> List[int]:
    return check_ceidg_state(fetch)[0]


def run_if_due(fetch: Optional[Callable[[str], dict]] = None) -> Optional[List[int]]:
    """Wykonuje sprawdzenie, jeśli jest na nie pora. None = nie było potrzeby."""
    if not _lock.acquire(blocking=False):
        return None
    try:
        if not is_check_due():
            return None
        if fetch is None:
            import os
            from app.utils.env_config import load_environment
            load_environment()
            if not (os.getenv("CEIDG_API_KEY") and os.getenv("CEIDG_BASE_URL")):
                return None
        marked = check_suspensions(fetch)
        set_setting(LAST_RUN_KEY, date.today().isoformat())
        return marked
    finally:
        _lock.release()


def run_in_background(on_done: Optional[Callable[[List[int]], None]] = None) -> threading.Thread:
    def _job():
        try:
            result = run_if_due()
            if result and on_done:
                on_done(result)
        except Exception:
            logger.warning("Sprawdzanie zawieszeń w CEIDG nie powiodło się", exc_info=True)

    t = threading.Thread(target=_job, name="ceidg-suspension", daemon=True)
    t.start()
    return t
