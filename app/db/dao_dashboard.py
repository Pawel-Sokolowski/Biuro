from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List

from app.db import dao_calendar
from app.db.dao_conn import get_conn

OPEN_TASK_STATUSES = ("Nowe", "W toku")
ACTIVE_COMPANY_SQL = "COALESCE(status, 'Aktywne') = 'Aktywne'"


def _scalar(conn, query: str, params: tuple = ()) -> int:
    row = conn.execute(query, params).fetchone()
    return int(row[0] or 0) if row else 0


def get_dashboard_data(pracownik_id: int, today: date | None = None) -> Dict[str, Any]:
    """Zbiera wszystkie dane widoczne na stronie głównej dla danego pracownika."""
    today = today or date.today()
    month = today.strftime("%Y-%m")
    placeholders = ",".join("%s" for _ in OPEN_TASK_STATUSES)
    conn = get_conn()
    try:
        data: Dict[str, Any] = {"miesiac": month}
        data["firmy_aktywne"] = _scalar(
            conn, f"SELECT COUNT(*) FROM firmy WHERE {ACTIVE_COMPANY_SQL}"
        )
        data["maile_nieprzeczytane"] = _scalar(
            conn,
            "SELECT COUNT(*) FROM wiadomosci_email "
            "WHERE COALESCE(przeczytana, 0) = 0 AND COALESCE(usunieta, 0) = 0",
        )
        data["zadania_otwarte"] = _scalar(
            conn,
            f"SELECT COUNT(*) FROM zadania WHERE pracownik_id = %s AND status IN ({placeholders})",
            (pracownik_id, *OPEN_TASK_STATUSES),
        )
        data["zadania_zalegle"] = _scalar(
            conn,
            f"SELECT COUNT(*) FROM zadania WHERE pracownik_id = %s AND status IN ({placeholders}) "
            "AND termin IS NOT NULL AND termin != '' AND substr(termin, 1, 10) < %s",
            (pracownik_id, *OPEN_TASK_STATUSES, today.isoformat()),
        )
        data["sesje_dzisiaj"] = _scalar(
            conn,
            "SELECT COUNT(*) FROM czasomierze WHERE pracownik_id = %s AND substr(rozpoczecie, 1, 10) = %s",
            (pracownik_id, today.isoformat()),
        )
        data["dokumenty_miesiac"] = _scalar(
            conn,
            "SELECT COUNT(*) FROM dokumenty WHERE substr(data_wyst, 1, 7) = %s",
            (month,),
        )
        data["sekundy_miesiac"] = _scalar(
            conn,
            "SELECT COALESCE(SUM(sekundy), 0) FROM czasomierze WHERE pracownik_id = %s AND miesiac = %s",
            (pracownik_id, month),
        )

        delivered = _scalar(
            conn,
            "SELECT COUNT(*) FROM statusy_miesieczne s JOIN firmy f ON f.id = s.firma_id "
            f"WHERE s.miesiac = %s AND s.dokumenty_dostarczone = 1 AND {ACTIVE_COMPANY_SQL.replace('status', 'f.status')}",
            (month,),
        )
        data["dokumenty_dostarczone"] = delivered
        data["dokumenty_brak"] = max(data["firmy_aktywne"] - delivered, 0)

        stages = {"Brak": 0, "Zatwierdzone": 0, "Zaksięgowane": 0, "Wysłane wyniki": 0}
        for row in conn.execute(
            "SELECT s.etap, COUNT(*) FROM statusy_miesieczne s JOIN firmy f ON f.id = s.firma_id "
            f"WHERE s.miesiac = %s AND {ACTIVE_COMPANY_SQL.replace('status', 'f.status')} GROUP BY s.etap",
            (month,),
        ):
            stages[row[0]] = int(row[1])
        stages["Brak"] = max(data["firmy_aktywne"] - sum(v for k, v in stages.items() if k != "Brak"), 0)
        data["etapy"] = stages

        data["zadania"] = [
            dict(r)
            for r in conn.execute(
                "SELECT z.id, z.tytul, z.status, z.termin, "
                "COALESCE(NULLIF(f.nazwa_skrocona, ''), f.nazwa) AS firma "
                "FROM zadania z LEFT JOIN firmy f ON f.id = z.firma_id "
                f"WHERE z.pracownik_id = %s AND z.status IN ({placeholders}) "
                "ORDER BY (z.termin IS NULL OR z.termin = ''), z.termin LIMIT 8",
                (pracownik_id, *OPEN_TASK_STATUSES),
            )
        ]
        data["brak_dokumentow"] = [
            dict(r)
            for r in conn.execute(
                "SELECT f.id, COALESCE(NULLIF(f.nazwa_skrocona, ''), f.nazwa) AS nazwa "
                "FROM firmy f LEFT JOIN statusy_miesieczne s "
                "ON s.firma_id = f.id AND s.miesiac = %s "
                f"WHERE {ACTIVE_COMPANY_SQL.replace('status', 'f.status')} "
                "AND COALESCE(s.dokumenty_dostarczone, 0) = 0 ORDER BY nazwa LIMIT 10",
                (month,),
            )
        ]
    finally:
        conn.close()

    start = today.isoformat()
    end = (today + timedelta(days=7)).isoformat()
    data["wydarzenia"] = dao_calendar.list_events(pracownik_id, start, end)[:8]
    return data


def format_duration(seconds: int) -> str:
    hours, rest = divmod(int(seconds or 0), 3600)
    return f"{hours} h {rest // 60:02d} min"


def format_event_time(event: Dict[str, Any]) -> str:
    start = event["start"]
    day = datetime.fromisoformat(start[:10]).strftime("%d.%m")
    if event.get("caly_dzien"):
        return f"{day}  cały dzień"
    return f"{day}  {start[11:16]}"
