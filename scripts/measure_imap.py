"""Pomiar czasu operacji IMAP na koncie zapisanym w aplikacji (tylko odczyt).

Uruchomienie z katalogu głównego projektu, na komputerze z bazą i kontem e-mail:
    python -m scripts.measure_imap [id_konta] [liczba_powtorzen]
Hasło jest odszyfrowywane lokalnie i nie jest wypisywane.
"""
import statistics
import sys
import time

from app.db.dao_conn import get_conn
from app.email import email_sync


def _account(konto_id):
    conn = get_conn()
    try:
        if konto_id:
            row = conn.execute("SELECT * FROM konta_email WHERE id = ?", (konto_id,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM konta_email WHERE aktywne = 1 ORDER BY id LIMIT 1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _timed(label, fn, repeats):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    print(f"{label}: mediana {statistics.median(samples):.0f} ms, min {min(samples):.0f}, max {max(samples):.0f}")


def main():
    konto_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
    repeats = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    account = _account(konto_id)
    if not account:
        print("Brak konta e-mail w bazie.")
        return

    def connect_logout():
        conn = email_sync._connect_imap(account, 30)
        if conn:
            conn.logout()

    _timed("połączenie + logowanie + wylogowanie", connect_logout, repeats)

    def connect_select_logout():
        conn = email_sync._connect_imap(account, 30)
        if conn:
            email_sync._select_folder(conn, "INBOX", readonly=True)
            conn.logout()

    _timed("połączenie + wybór INBOX + wylogowanie (koszt jednej operacji na wiadomości)", connect_select_logout, repeats)

    conn = email_sync._connect_imap(account, 30)
    if not conn:
        print("Nie udało się połączyć.")
        return
    try:
        email_sync._select_folder(conn, "INBOX", readonly=True)
        _timed("NOOP na otwartym połączeniu (sam RTT)", conn.noop, repeats)
        start = time.perf_counter()
        typ, data = conn.uid("SEARCH", None, "ALL")
        uids = data[0].split() if typ == "OK" and data and data[0] else []
        print(f"SEARCH ALL: {(time.perf_counter() - start) * 1000:.0f} ms, wiadomości w INBOX: {len(uids)}")
        if uids:
            chunk = b",".join(uids[-email_sync.HEADER_BATCH:]).decode()
            start = time.perf_counter()
            conn.uid("FETCH", chunk, "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID CONTENT-TYPE)])")
            print(f"FETCH nagłówków paczki ({min(len(uids), email_sync.HEADER_BATCH)} szt.): "
                  f"{(time.perf_counter() - start) * 1000:.0f} ms")
    finally:
        conn.logout()


if __name__ == "__main__":
    main()
