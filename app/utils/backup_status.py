"""Status ostatniej kopii zapasowej na podstawie logu programu do backupu."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

from app.db.dao_app_settings import get_setting

LOG_PATH_KEY = "backup_log_path"
MAX_AGE_KEY = "backup_max_age_hours"
DEFAULT_MAX_AGE_HOURS = 36
LOG_SUFFIXES = {".log", ".txt"}

LINE_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?)\s+(.*)$")
SUCCESS_RE = re.compile(r"has been compressed|został[oa]? skompresowan|backup completed", re.I)
ERROR_RE = re.compile(r"error|failed|denied|cannot|can't|could not|unable to|błąd|nie udało", re.I)
IGNORED_RE = re.compile(r"timed out|update|new versions?|checking for", re.I)
FILE_IN_QUOTES_RE = re.compile(r'"([^"]+\.(?:bak|zip|7z|sql))"', re.I)
DB_ERROR_RE = re.compile(r"(?:cannot|can't|could not|unable to)\s+connect\s+to\s+[\"']?([^\s\"',;]+)", re.I)
DONE_TASK_RE = re.compile(r"\bbackup done\b|\bdone for the task\b", re.I)
DONE_RE = re.compile(r'The file "([^"]+)" has been compressed', re.I)
START_RE = re.compile(r'Compressing the file "([^"]+)"', re.I)
DATE_SUFFIX_RE = re.compile(r"[_\- ]?\d{8}(?:[_\-]?\d{4,6})?$")


def backup_name(path: str) -> str:
    """Nazwa bazy z nazwy pliku, np. E:\\x\\Monika_20261014.BAK -> Monika."""
    stem = re.split(r"[\\/]", path)[-1].rsplit(".", 1)[0]
    return DATE_SUFFIX_RE.sub("", stem).strip("_ -") or stem


def backup_file_date(path: str) -> Optional[date]:
    """Data zakodowana w nazwie pliku, np. Monika_20261014.BAK -> 2026-10-14."""
    stem = re.split(r"[\\/]", path)[-1].rsplit(".", 1)[0]
    match = DATE_SUFFIX_RE.search(stem)
    if not match:
        return None
    digits = re.search(r"\d{8}", match.group(0)).group(0)
    try:
        return datetime.strptime(digits, "%Y%m%d").date()
    except ValueError:
        return None


def _fold(text: str) -> str:
    table = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
    return re.sub(r"[^a-z0-9]+", " ", text.translate(table).lower()).strip()


def match_company(name: str, companies: list) -> Optional[str]:
    """Dopasowuje nazwę bazy do firmy, gdy wszystkie jej człony występują w nazwie firmy."""
    tokens = _fold(name).split()
    if not tokens:
        return None
    for company in companies:
        for field in ("nazwa", "nazwa_skrocona", "nazwa_saldeo"):
            haystack = _fold(company.get(field) or "")
            if haystack and all(token in haystack.split() for token in tokens):
                return company.get("nazwa") or company.get(field)
    for company in companies:
        for field in ("nazwa", "nazwa_skrocona", "nazwa_saldeo"):
            words = _fold(company.get(field) or "").split()
            if words and all(any(w.startswith(t) for w in words) for t in tokens if len(t) >= 3):
                if any(len(t) >= 3 for t in tokens):
                    return company.get("nazwa") or company.get(field)
    return None


def _parse_time(text: str) -> datetime:
    fmt = "%Y-%m-%d %H:%M:%S" if text.count(":") == 2 else "%Y-%m-%d %H:%M"
    return datetime.strptime(text, fmt)


def _error_database(message: str, known: set) -> Optional[str]:
    """Nazwa bazy, której dotyczy błąd; None, gdy komunikat nie wskazuje żadnej bazy
    (np. błędy samego programu backupującego, jak sprawdzanie nowych wersji)."""
    found = DB_ERROR_RE.search(message)
    if found:
        return backup_name(found.group(1))
    path = FILE_IN_QUOTES_RE.search(message)
    if path:
        return backup_name(path.group(1))
    lowered = message.lower()
    for name in known:
        if name.lower() in lowered:
            return name
    return None


def parse_backup_log(text: str) -> Dict[str, Any]:
    """Zwraca czas ostatniej udanej kopii i informację, czy po niej wystąpił błąd."""
    last_success: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    error_after_success: Optional[str] = None
    files = 0
    started: Dict[str, Optional[datetime]] = {}
    done: Dict[str, datetime] = {}
    file_dates: Dict[str, Optional[date]] = {}
    db_errors: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = LINE_RE.match(line)
        message = match.group(2) if match else line
        stamp = _parse_time(match.group(1)) if match else None
        if stamp:
            last_seen = stamp
        start_match = START_RE.search(message)
        if start_match:
            name = backup_name(start_match.group(1))
            started[name] = stamp
            file_dates[name] = backup_file_date(start_match.group(1)) or file_dates.get(name)
        done_match = DONE_RE.search(message)
        if done_match and stamp:
            name = backup_name(done_match.group(1))
            done[name] = stamp
            db_errors.pop(name, None)
            file_dates[name] = backup_file_date(done_match.group(1)) or file_dates.get(name)
        if match and SUCCESS_RE.search(message):
            if last_success is None or stamp.date() != last_success.date():
                files = 0
            last_success = stamp
            files += 1
            error_after_success = None
        elif not DONE_TASK_RE.search(message) and not IGNORED_RE.search(message) and ERROR_RE.search(message):
            db_name = _error_database(message, set(started) | set(done))
            if db_name:
                error_after_success = message
                db_errors[db_name] = message
    return {
        "last_success": last_success,
        "last_seen": last_seen,
        "files": files,
        "error": error_after_success,
        "databases": {
            name: {"last_success": done.get(name), "started": started.get(name),
                   "file_date": file_dates.get(name), "error": db_errors.get(name)}
            for name in sorted(set(started) | set(done) | set(db_errors), key=str.lower)
        },
    }


def find_log_file(path: str) -> Optional[Path]:
    """Przyjmuje plik logu albo folder; w folderze wybiera najnowszy log."""
    if not path:
        return None
    target = Path(path).expanduser()
    if target.is_file():
        return target
    if target.is_dir():
        logs = [p for p in target.iterdir() if p.is_file() and p.suffix.lower() in LOG_SUFFIXES]
        return max(logs, key=lambda p: p.stat().st_mtime, default=None)
    return None


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "cp1250"):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _company_rows() -> list:
    from app.db.dao_conn import get_conn
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute("SELECT id, nazwa, nazwa_skrocona, nazwa_saldeo FROM firmy")]
    finally:
        conn.close()


def save_backup_dates_to_companies(status: Dict[str, Any]) -> Dict[str, Any]:
    """Zapisuje w firmach datę ostatniej kopii (z nazwy pliku, a gdy jej brak - z logu)."""
    from app.db.dao_conn import get_conn

    companies = _company_rows()
    by_name = {c["nazwa"]: c["id"] for c in companies}
    best: Dict[int, tuple] = {}
    unmatched = []
    for db in status.get("bazy", []):
        firma_id = by_name.get(db.get("firma"))
        saved = db.get("ostatnia_kopia")
        if firma_id is None:
            unmatched.append(db["nazwa"])
            continue
        if saved is None:
            continue
        day = db.get("data_bazy") or saved.date()
        key = (day, saved)
        if firma_id not in best or key > best[firma_id][0]:
            best[firma_id] = (key, day, saved)
    conn = get_conn()
    try:
        for firma_id, (_, day, saved) in best.items():
            conn.execute(
                "UPDATE firmy SET data_ostatniej_kopii = %s, zapis_ostatniej_kopii = %s WHERE id = %s",
                (day.strftime("%Y-%m-%d"), saved.strftime("%Y-%m-%d %H:%M"), firma_id),
            )
        conn.commit()
    finally:
        conn.close()
    return {"zapisano": len(best), "bez_firmy": unmatched}


def stored_backup_dates() -> Dict[int, tuple]:
    from app.db.dao_conn import get_conn

    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, data_ostatniej_kopii, zapis_ostatniej_kopii FROM firmy "
            "WHERE data_ostatniej_kopii IS NOT NULL"
        ).fetchall()
        return {r["id"]: (r["data_ostatniej_kopii"], r["zapis_ostatniej_kopii"]) for r in rows}
    finally:
        conn.close()


def get_backup_status(now: Optional[datetime] = None, from_mail: bool = False) -> Dict[str, Any]:
    """Stan: brak_konfiguracji, brak_logu, brak_kopii, ok, nieaktualna, blad.

    from_mail=True ignoruje ścieżkę pliku logu i bierze log z ostatniego maila."""
    now = now or datetime.now()
    path = "" if from_mail else (get_setting(LOG_PATH_KEY) or "").strip()
    result: Dict[str, Any] = {
        "stan": "brak_konfiguracji", "ostatnia_kopia": None, "wiek_godzin": None,
        "pliki": 0, "bazy": [], "komunikat": "Brak danych o kopii zapasowej.",
    }
    log_text = None
    if path:
        log_file = find_log_file(path)
        if log_file is None:
            result.update(stan="brak_logu", komunikat=f"Nie znaleziono logu kopii: {path}")
            return result
        try:
            log_text = _read_text(log_file)
        except OSError as exc:
            result.update(stan="brak_logu", komunikat=f"Nie można odczytać logu: {exc}")
            return result
    else:
        log_text = get_setting("backup_mail_log_text") or ""
        if not log_text:
            result.update(
                komunikat="Brak logu kopii — skonfiguruj konto systemowe "
                          "lub kliknij „Odśwież kopie z maila”.")
            return result
    parsed = parse_backup_log(log_text)
    last = parsed["last_success"]
    if last is None:
        result.update(stan="brak_kopii", komunikat="W logu nie znaleziono udanej kopii.")
        return result
    try:
        max_age = float(get_setting(MAX_AGE_KEY) or DEFAULT_MAX_AGE_HOURS)
    except ValueError:
        max_age = DEFAULT_MAX_AGE_HOURS
    age = (now - last).total_seconds() / 3600
    companies = _company_rows()
    bazy = []
    for name, info in parsed["databases"].items():
        when = info["last_success"]
        db_age = (now - when).total_seconds() / 3600 if when else None
        if when is None or info.get("error"):
            state = "blad"
        elif db_age > max_age:
            state = "nieaktualna"
        else:
            state = "ok"
        bazy.append({
            "nazwa": name, "firma": match_company(name, companies),
            "ostatnia_kopia": when, "wiek_godzin": db_age, "stan": state,
            "data_bazy": info.get("file_date"),
        })
    result.update(ostatnia_kopia=last, wiek_godzin=age, pliki=parsed["files"], bazy=bazy)
    stamp = last.strftime("%Y-%m-%d %H:%M")
    if parsed["error"]:
        result.update(stan="blad", komunikat=f"Ostatnia kopia: {stamp}. Po niej błąd: {parsed['error']}")
    elif age > max_age:
        result.update(stan="nieaktualna", komunikat=f"Ostatnia kopia: {stamp} (ponad {int(max_age)} h temu!)")
    else:
        result.update(stan="ok", komunikat=f"Ostatnia kopia: {stamp} ({parsed['files']} plików)")
    return result
