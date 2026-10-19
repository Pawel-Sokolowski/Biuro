import os
import shutil
import subprocess
from urllib.parse import urlparse, unquote

from app.config import get_dsn


def dump_database(target_path: str) -> None:
    """Zapisuje kopię bazy przez pg_dump (format custom); wymaga pg_dump w PATH."""
    exe = shutil.which("pg_dump")
    if not exe:
        raise RuntimeError("Nie znaleziono pg_dump w PATH (zainstaluj narzędzia klienckie PostgreSQL).")
    u = urlparse(get_dsn())
    env = dict(os.environ)
    if u.password:
        env["PGPASSWORD"] = unquote(u.password)
    cmd = [
        exe, "-Fc", "-f", target_path,
        "-h", u.hostname or "localhost", "-p", str(u.port or 5432),
        "-U", unquote(u.username or ""), (u.path or "/").lstrip("/"),
    ]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or "pg_dump zakończył się błędem")
