"""Cykliczne sprawdzanie w CEIDG, czy działalność firmy jest zawieszona."""
from __future__ import annotations

import logging
import threading
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional

from app.db.dao_app_settings import get_setting, set_setting
from app.db.dao_companies import set_company_status
from app.db.dao_conn import get_conn

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
            "SELECT id, nip FROM firmy "
            "WHERE status = 'Aktywne' AND COALESCE(TRIM(nip), '') != ''"
        ).fetchall()
    finally:
        conn.close()
    return [{"id": r[0], "nip": r[1]} for r in rows]


def check_suspensions(fetch: Optional[Callable[[str], dict]] = None) -> List[int]:
    """Oznacza jako zawieszone aktywne firmy, które CEIDG ma jako ZAWIESZONY.
    Zwraca id oznaczonych firm. Błędy pojedynczych zapytań są pomijane."""
    if fetch is None:
        from app.companies.ceidg_api import get_ceidg_firma_by_nip as fetch

    marked: List[int] = []
    for firm in _candidates():
        nip = "".join(ch for ch in str(firm["nip"]) if ch.isdigit())
        if len(nip) != 10:
            continue
        try:
            data = fetch(nip)
        except Exception:
            logger.warning("CEIDG: błąd zapytania dla firmy %s", firm["id"], exc_info=True)
            continue
        if not data or data.get("error"):
            continue
        if str(data.get("status") or "").upper() in SUSPENDED_STATUSES:
            since = data.get("data_zawieszenia") or date.today().isoformat()
            set_company_status(firm["id"], "Zawieszone", since)
            marked.append(firm["id"])
    return marked


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
