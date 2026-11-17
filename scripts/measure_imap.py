"""Pomiar kosztu operacji IMAP na koncie zapisanym w aplikacji."""
from __future__ import annotations

import statistics
import sys
import time

from app.db.dao_conn import get_conn
from app.email import email_sync


def _account(konto_id):
    with get_conn() as conn:
        if konto_id:
            row = conn.execute(
                "SELECT * FROM konta_email WHERE id = %s",
                (konto_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM konta_email "
                "WHERE aktywne = 1 ORDER BY id LIMIT 1"
            ).fetchone()
    return dict(row) if row else None


def _timed(label, fn, repeats=1):
    samples = []
    result = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = fn()
        samples.append((time.perf_counter() - started) * 1000)
    print(
        f"{label}: mediana {statistics.median(samples):.0f} ms, "
        f"min {min(samples):.0f}, max {max(samples):.0f}"
    )
    return result


def main():
    konto_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
    repeats = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    account = _account(konto_id)
    if not account:
        raise SystemExit("Brak aktywnego konta e-mail w bazie.")
    konto_id = int(account["id"])
    print(f"Konto: #{konto_id} {account.get('email') or ''}")

    def fresh_connection():
        conn = email_sync._connect_imap(account, 30)
        if conn is None:
            raise RuntimeError("Nie udało się połączyć z IMAP.")
        conn.logout()

    _timed("nowe połączenie + login + logout", fresh_connection, repeats)

    with email_sync._shared_session(konto_id, 30) as session:
        if session is None:
            raise SystemExit("Nie udało się otworzyć współdzielonej sesji IMAP.")
        _timed("NOOP na istniejącej sesji", session.conn.noop, repeats)
        _timed("LIST folderów", session.conn.list, repeats)
        folders = email_sync.selectable_folder_names(konto_id)
        if "INBOX" not in folders:
            folders.insert(0, "INBOX")
        print(f"Foldery w bazie: {len(folders)}")
        for folder in folders:
            status = _timed(
                f"STATUS {folder}",
                lambda f=folder: email_sync._folder_status(session, f),
            )
            if folder == "INBOX":
                if not session.select(folder, True):
                    continue
                _timed(
                    "INBOX SEARCH ALL",
                    lambda: session.conn.uid("SEARCH", None, "ALL"),
                )
                _timed(
                    "INBOX SEARCH UNSEEN",
                    lambda: session.conn.uid("SEARCH", None, "UNSEEN"),
                )
            if status is None:
                print(f"  UWAGA: serwer nie zwrócił STATUS dla {folder}")


if __name__ == "__main__":
    main()
