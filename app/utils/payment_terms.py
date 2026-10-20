# app/utils/payment_terms.py
"""Domyślny termin płatności faktur: liczba dni od wystawienia albo stały dzień miesiąca."""
import calendar
from datetime import date, timedelta
from typing import Optional

from app.db.dao_app_settings import get_setting_first

MODE_KEY = "termin_platnosci_tryb"
DAYS_KEY = "termin_platnosci_dni"
DAY_OF_MONTH_KEY = "termin_platnosci_dzien"
MODE_DAYS = "dni"
MODE_DAY_OF_MONTH = "dzien"


def due_date_for(issue: date, mode: str, days: int, day_of_month: int) -> date:
    if mode == MODE_DAY_OF_MONTH:
        year, month = issue.year, issue.month
        last = calendar.monthrange(year, month)[1]
        if min(day_of_month, last) < issue.day:
            month += 1
            if month > 12:
                year, month = year + 1, 1
            last = calendar.monthrange(year, month)[1]
        return date(year, month, min(day_of_month, last))
    return issue + timedelta(days=days)


def _int_setting(keys, default: int) -> int:
    try:
        return int(str(get_setting_first(keys, str(default))).strip())
    except (TypeError, ValueError):
        return default


def default_due_date(issue: Optional[date] = None, default_days: int = 7) -> date:
    issue = issue or date.today()
    mode = get_setting_first((MODE_KEY,), MODE_DAYS)
    days = _int_setting((DAYS_KEY, "faktury_termin_dni"), default_days)
    day = min(max(_int_setting((DAY_OF_MONTH_KEY,), issue.day), 1), 31)
    return due_date_for(issue, mode, days, day)
