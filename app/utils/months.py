from __future__ import annotations

from datetime import date, datetime, timedelta


def previous_month(month: str) -> str:
    year, month_number = (int(part) for part in month.split("-", 1))
    if month_number == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month_number - 1:02d}"


def default_month_for_monthly_view(today: date | None = None) -> str:
    """Use the previous month until the last calendar day of the current month."""
    today = today or date.today()
    current = f"{today.year:04d}-{today.month:02d}"
    if today.day < _last_day_of_month(today.year, today.month):
        return previous_month(current)
    return current


def _last_day_of_month(year: int, month: int) -> int:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return (next_month - timedelta(days=1)).day


def month_bounds(month: str) -> tuple[str, str]:
    """Return an inclusive start and exclusive end ISO date for YYYY-MM."""
    start = datetime.strptime(month, "%Y-%m")
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
