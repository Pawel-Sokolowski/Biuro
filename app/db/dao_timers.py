from app.remote.boundary import endpoint as office_endpoint
from typing import Any, Dict, Iterable, List, Optional, Tuple
from datetime import datetime
import json

from app.db.dao_conn import get_conn

SOURCE_TIMER = "stoper"
SOURCE_IMPORT = "import"
_STATUS_COLUMNS = {"ksiegowosc": "sekundy_ksiegowosc", "kadry": "sekundy_kadry"}


def _timer_rows(conn, column, value, month=None):
    # column is selected by the two business operations below, never by a client.
    if column not in ('firma_id', 'pracownik_id'):
        raise ValueError('Nieprawidłowy filtr czasu.')
    params = [value]
    suffix = ''
    if month:
        datetime.strptime(month, '%Y-%m')
        suffix = ' AND c.miesiac = %s'
        params.append(month)
    rows = [
        {"zrodlo": SOURCE_TIMER, "id": r["id"], "firma_id": r["firma_id"],
         "firma": r["firma"] or "Bez firmy", "pracownik_id": r["pracownik_id"],
         "miesiac": r["miesiac"], "start": r["rozpoczecie"], "koniec": r["zakonczenie"],
         "pracownik": r["pracownik"] or "", "dzial": r["typ"], "opis": r["opis"] or "",
         "sekundy": int(r["sekundy"] or 0), "pauza_sekundy": 0,
         "usluga_id": r["usluga_id"], "usluga": r["usluga"] or ""}
        for r in conn.execute(
            "SELECT c.*, u.nazwa AS usluga, p.imie || ' ' || p.nazwisko AS pracownik, "
            "COALESCE(NULLIF(TRIM(f.nazwa_skrocona),''),f.nazwa) AS firma "
            "FROM czasomierze c LEFT JOIN pracownicy p ON p.id=c.pracownik_id "
            "LEFT JOIN firmy f ON f.id=c.firma_id LEFT JOIN uslugi_dodatkowe u ON u.id=c.usluga_id "
            f"WHERE c.{column}=%s" + suffix, params).fetchall()]
    rows += [
        {"zrodlo": SOURCE_IMPORT, "id": r["id"], "firma_id": r["firma_id"],
         "firma": r["firma"] or r["firma_nazwa"] or "Bez firmy", "pracownik_id": r["pracownik_id"],
         "miesiac": r["miesiac"], "start": f"{r['data']} {r['od'] or ''}".strip(),
         "koniec": f"{r['data']} {r['do_'] or ''}".strip(),
         "pracownik": r["pracownik_nazwa"] or "", "dzial": r["dzial"],
         "opis": " ".join(filter(None, [r["czynnosc"], r["komentarz"]])),
         "sekundy": int(r["sekundy"] or 0), "pauza_sekundy": int(r["pauza_sekundy"] or 0),
         "usluga_id": None, "usluga": ""}
        for r in conn.execute(
            "SELECT c.*, COALESCE(NULLIF(TRIM(f.nazwa_skrocona),''),f.nazwa) AS firma "
            "FROM import_czas_pracy c LEFT JOIN firmy f ON f.id=c.firma_id "
            f"WHERE c.{column}=%s" + suffix, params).fetchall()]
    rows.sort(key=lambda r: (str(r["start"]).replace('T',' '), r["id"]), reverse=True)
    return rows


@office_endpoint('app.db.dao_timers.list_company_timers')
def list_company_timers(firma_id: int) -> List[Dict[str, Any]]:
    """Rekordy czasu firmy z obu źródeł, od najnowszych."""
    with get_conn() as conn:
        return _timer_rows(conn, 'firma_id', firma_id)


def _check_employee_access(employee_id, actor_id):
    from app.security.authorization import can
    if not actor_id or (employee_id != actor_id and not can(actor_id, 'can_view_finances')):
        raise PermissionError('Możesz zmieniać własny czas. Czas innych osób wymaga uprawnień finansowych.')


@office_endpoint('app.db.dao_timers.list_employee_timers')
def list_employee_timers(employee_id: int, actor_id: int, month: Optional[str] = None):
    """Własny czas ze wszystkich firm, także stoper ogólny; bez dopasowania po nazwisku."""
    _check_employee_access(employee_id, actor_id)
    with get_conn() as conn:
        return _timer_rows(conn, 'pracownik_id', employee_id, month)


def _record(conn, source, record_id, firma_id, actor_id):
    tables = {SOURCE_TIMER: 'czasomierze', SOURCE_IMPORT: 'import_czas_pracy'}
    if source not in tables:
        raise ValueError('Nieznane źródło rekordu czasu.')
    row = conn.execute(f"SELECT * FROM {tables[source]} WHERE id=%s AND firma_id IS NOT DISTINCT FROM %s FOR UPDATE",
                       (record_id, firma_id)).fetchone()
    if row:
        _check_employee_access(row['pracownik_id'], actor_id)
    return tables[source], row


def _range(started_at, ended_at, source, pause=0):
    if started_at is None and ended_at is None:
        return None
    if not started_at or not ended_at:
        raise ValueError('Podaj początek i koniec czasu pracy.')
    try:
        start, end = datetime.fromisoformat(started_at), datetime.fromisoformat(ended_at)
        seconds = int((end-start).total_seconds())
    except (TypeError, ValueError):
        raise ValueError('Nieprawidłowa data lub godzina.') from None
    if start.tzinfo is not None or end.tzinfo is not None:
        raise ValueError('Podaj godziny lokalne bez strefy czasowej.')
    if seconds <= 0 or seconds > 999*3600:
        raise ValueError('Koniec musi być późniejszy niż początek (maksymalnie 999 godzin).')
    if source == SOURCE_IMPORT and start.date() != end.date():
        raise ValueError('Zaimportowany rekord musi mieścić się w jednym dniu.')
    if pause > seconds:
        raise ValueError('Przerwa jest dłuższa niż wskazany przedział czasu.')
    return start, end, seconds-pause


def _adjust_totals(conn, firma_id, month, department, delta):
    column = _STATUS_COLUMNS.get(department)
    if firma_id is None or not column or not delta:
        return
    conn.execute('INSERT INTO statusy_miesieczne (firma_id,miesiac) VALUES (%s,%s) ON CONFLICT (firma_id,miesiac) DO NOTHING',
                 (firma_id, month))
    conn.execute(f"UPDATE statusy_miesieczne SET {column}=GREATEST(0,COALESCE({column},0)+%s) WHERE firma_id=%s AND miesiac=%s",
                 (delta, firma_id, month))


def _audit(conn, table, record_id, actor_id, action, old, new=None):
    details = {'before': dict(old), 'after': new} if new is not None else {'before': dict(old)}
    conn.execute('INSERT INTO audit_log (tabela,rekord_id,akcja,uzytkownik_id,szczegoly) VALUES (%s,%s,%s,%s,%s)',
                 (table, record_id, action, actor_id, json.dumps(details,ensure_ascii=False,default=str)))


DEPARTMENTS = [
    ("ksiegowosc", "Księgowość"),
    ("kadry", "Kadry"),
    ("dodatkowe", "Czynności dodatkowe"),
    ("ogolne", "Inne"),
]


@office_endpoint('app.db.dao_timers.add_manual_time')
def add_manual_time(employee_id: int, firma_id: Optional[int], dzial: str, opis: str,
                    sekundy: int, actor_id: Optional[int], usluga_id: Optional[int] = None,
                    *, started_at: Optional[str] = None, miesiac: Optional[str] = None) -> int:
    if not actor_id:
        raise PermissionError('Brak zalogowanego pracownika.')
    _check_employee_access(employee_id, actor_id)
    if dzial not in dict(DEPARTMENTS):
        raise ValueError('Nieznany dział.')
    sekundy = int(sekundy)
    if sekundy <= 0 or sekundy > 999 * 3600:
        raise ValueError('Czas musi wynosić od 1 sekundy do 999 godzin.')
    opis = (opis or '').strip()
    if dzial == 'dodatkowe' and not usluga_id:
        raise ValueError('Dla czynności dodatkowej wybierz usługę.')
    if dzial != 'dodatkowe':
        usluga_id = None
    try:
        start = datetime.fromisoformat(started_at) if started_at else datetime.now().replace(microsecond=0)
    except (TypeError, ValueError):
        raise ValueError('Nieprawidłowa data lub godzina rozpoczęcia.') from None
    if start.tzinfo is not None:
        raise ValueError('Podaj czas lokalny bez strefy czasowej.')
    from datetime import timedelta
    end = start + timedelta(seconds=sekundy)
    if miesiac is None:
        miesiac = start.strftime('%Y-%m')
    try:
        datetime.strptime(miesiac, '%Y-%m')
    except (TypeError, ValueError):
        raise ValueError('Nieprawidłowy miesiąc rozliczeniowy.') from None
    with get_conn() as conn:
        employee = conn.execute(
            'SELECT id FROM pracownicy WHERE id=%s AND COALESCE(aktywny,1)=1',
            (employee_id,),
        ).fetchone()
        if not employee:
            raise ValueError('Wybrany pracownik nie istnieje lub jest nieaktywny.')
        if firma_id is not None and not conn.execute(
                'SELECT 1 FROM firmy WHERE id=%s', (firma_id,)).fetchone():
            raise ValueError('Wybrana firma nie istnieje.')
        row = conn.execute(
            "INSERT INTO czasomierze (firma_id,pracownik_id,miesiac,typ,usluga_id,fakturowac,"
            "rozpoczecie,zakonczenie,sekundy,opis) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (firma_id, employee_id, miesiac, dzial, usluga_id, int(dzial == 'dodatkowe'),
             start.isoformat(), end.isoformat(), sekundy, opis),
        ).fetchone()
        record_id = row['id']
        _adjust_totals(conn, firma_id, miesiac, dzial, sekundy)
        new = dict(conn.execute('SELECT * FROM czasomierze WHERE id=%s', (record_id,)).fetchone())
        _audit(conn, 'czasomierze', record_id, actor_id, 'insert_manual', {}, new)
        conn.commit()
        return record_id


@office_endpoint('app.db.dao_timers.list_extra_service_catalogue')
def list_extra_service_catalogue() -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        return [{"id": r["id"], "nazwa": r["nazwa"]}
                for r in conn.execute("SELECT id, nazwa FROM uslugi_dodatkowe ORDER BY nazwa").fetchall()]
    finally:
        conn.close()


@office_endpoint('app.db.dao_timers.add_extra_service_to_catalogue')
def add_extra_service_to_catalogue(nazwa: str, actor_id: Optional[int], cena_netto: float = 0.0,
                                   stawka_vat: str = "23") -> int:
    """Dodaje czynność dodatkową do katalogu (lub zwraca istniejącą o tej nazwie)."""
    if not actor_id:
        raise PermissionError("Brak zalogowanego pracownika.")
    nazwa = (nazwa or "").strip()
    if not nazwa:
        raise ValueError("Nazwa czynności jest wymagana.")
    conn = get_conn()
    try:
        row = conn.execute("SELECT id FROM uslugi_dodatkowe WHERE LOWER(nazwa) = LOWER(%s)", (nazwa,)).fetchone()
        if row:
            return row["id"]
        row = conn.execute(
            "INSERT INTO uslugi_dodatkowe (nazwa, cena_netto, stawka_vat, kategoria) "
            "VALUES (%s, %s, %s, 'księgowość') RETURNING id", (nazwa, cena_netto, stawka_vat)).fetchone()
        conn.commit()
        return row["id"]
    finally:
        conn.close()


@office_endpoint('app.db.dao_timers.update_company_timer')
def update_company_timer(firma_id: Optional[int], source: str, record_id: int, dzial: str, opis: str,
                         sekundy: int, actor_id: Optional[int], usluga_id: Optional[int] = None,
                         *, started_at: Optional[str] = None, ended_at: Optional[str] = None,
                         miesiac: Optional[str] = None) -> bool:
    """Atomowa edycja własnego czasu, dat i sum miesięcznych, z historią zmian."""
    if not actor_id:
        raise PermissionError('Brak zalogowanego pracownika.')
    if dzial not in dict(DEPARTMENTS):
        raise ValueError('Nieznany dział.')
    sekundy = int(sekundy)
    if sekundy < 0 or sekundy > 999*3600:
        raise ValueError('Czas musi wynosić od 0 do 999 godzin.')
    opis = (opis or '').strip()
    with get_conn() as conn:
        table, old = _record(conn, source, record_id, firma_id, actor_id)
        if not old:
            return False
        interval = _range(started_at, ended_at, source, int(old['pauza_sekundy'] or 0) if source==SOURCE_IMPORT else 0)
        month = miesiac or (interval[0].strftime('%Y-%m') if interval else old['miesiac'])
        try:
            datetime.strptime(month, '%Y-%m')
        except (TypeError, ValueError):
            raise ValueError('Nieprawidłowy miesiąc rozliczeniowy.') from None
        if interval:
            sekundy = interval[2]
        if source == SOURCE_IMPORT:
            conn.execute('UPDATE import_czas_pracy SET dzial=%s,czynnosc=%s,komentarz=NULL,sekundy=%s,miesiac=%s WHERE id=%s',
                         (dzial, opis, sekundy, month, record_id))
            if interval:
                start, end, _ = interval
                conn.execute('UPDATE import_czas_pracy SET data=%s,od=%s,do_=%s WHERE id=%s',
                             (start.date().isoformat(),start.time().isoformat(),end.time().isoformat(),record_id))
        else:
            conn.execute('UPDATE czasomierze SET typ=%s,opis=%s,sekundy=%s,usluga_id=%s,miesiac=%s WHERE id=%s',
                         (dzial,opis,sekundy,usluga_id if dzial=='dodatkowe' else None,month,record_id))
            if interval:
                conn.execute('UPDATE czasomierze SET rozpoczecie=%s,zakonczenie=%s WHERE id=%s',
                             (interval[0].isoformat(),interval[1].isoformat(),record_id))
            _adjust_totals(conn,firma_id,old['miesiac'],old['typ'],-int(old['sekundy'] or 0))
            _adjust_totals(conn,firma_id,month,dzial,sekundy)
        new = dict(conn.execute(f'SELECT * FROM {table} WHERE id=%s',(record_id,)).fetchone())
        _audit(conn,table,record_id,actor_id,'update',old,new)
        conn.commit()
    return True


@office_endpoint('app.db.dao_timers.delete_company_timers')
def delete_company_timers(firma_id: Optional[int], items: Iterable[Tuple[str, int]], actor_id: Optional[int]) -> int:
    """Usuwa własne rekordy (lub cudze za uprawnieniem), korygując sumy w tej samej transakcji."""
    if not actor_id:
        raise PermissionError('Brak zalogowanego pracownika.')
    deleted = 0
    with get_conn() as conn:
        for source, record_id in items:
            table, row = _record(conn,source,record_id,firma_id,actor_id)
            if not row:
                continue
            conn.execute(f'DELETE FROM {table} WHERE id=%s',(record_id,))
            if source==SOURCE_TIMER:
                _adjust_totals(conn,firma_id,row['miesiac'],row['typ'],-int(row['sekundy'] or 0))
            _audit(conn,table,record_id,actor_id,'delete',row)
            deleted += 1
        conn.commit()
    return deleted
