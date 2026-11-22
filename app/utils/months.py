from __future__ import annotations

from datetime import date, datetime, timedelta
from calendar import monthrange


def previous_month(month: str) -> str:
    year, month_number = (int(part) for part in month.split("-", 1))
    if month_number == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month_number - 1:02d}"


def current_month(today: date | None = None) -> str:
    today = today or date.today()
    return today.isoformat()[:7]


def default_month_for_department(department: str, today: date | None = None) -> str:
    if department == "accounting":
        return previous_month(current_month(today))
    if department == "payroll":
        return current_month(today)
    raise ValueError("Nieprawidłowy dział.")
def default_month_for_employee(employee_id: int, today: date | None = None) -> str:
    from app.preferences.department import default_department
    return default_month_for_department(default_department(employee_id), today)




def default_month_for_monthly_view(today: date | None = None) -> str:
    """Use the previous month until the last calendar day of the current month."""
    today = today or date.today()
    current = current_month(today)
    if today.day < monthrange(today.year, today.month)[1]:
        return previous_month(current)
    return current


def month_bounds(month: str) -> tuple[str, str]:
    """Return an inclusive start and exclusive end ISO date for YYYY-MM."""
    start = datetime.strptime(month, "%Y-%m")
    try:
        end = start + timedelta(days=monthrange(start.year, start.month)[1])
    except OverflowError as exc:
        raise ValueError("Następny miesiąc wykracza poza zakres kalendarza.") from exc
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
