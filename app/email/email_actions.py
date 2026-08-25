# app/email/email_actions.py
import os
import tempfile
from PyQt6.QtWidgets import QFileDialog
from PyQt6.QtCore import QRunnable, QObject, pyqtSignal

from app.db.dao_companies import get_conn
from app.email.email_sync import (
    EmailSyncer,
    list_attachments,
    download_attachment,
    move_email_to_folder,
    load_email_message
)


class email_actions:

    # ============================================================
    # SYNC — wywołuje EmailSyncer
    # ============================================================
    @staticmethod
    def sync_all_emails(pracownik_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id FROM konta_email WHERE pracownik_id = ?", (pracownik_id,))
        accounts = cur.fetchall()
        conn.close()

        ok = True
        for (konto_id,) in accounts:
            if not EmailSyncer.sync_emails(konto_id, pracownik_id):
                ok = False
        return ok

    # ============================================================
    # KONT
    # ============================================================
    @staticmethod
    def load_accounts(pracownik_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, email FROM konta_email WHERE pracownik_id = ?", (pracownik_id,))
        rows = cur.fetchall()
        conn.close()
        return rows

    # ============================================================
    # FOLDERY IMAP
    # ============================================================
    @staticmethod
    def load_folders(konto_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT serwer_imap, port_imap, haslo_enc, email FROM konta_email WHERE id = ?", (konto_id,))
        row = cur.fetchone()
        conn.close()

        if not row:
            return []

        imap = EmailSyncer.connect_imap(row[1], row[2], row[3], row[2])
        if not imap:
            return []

        status, folders = imap.list()
        imap.logout()

        out = []
        if status == "OK":
            for f in folders:
                parts = f.decode().split(' "/" ')
                if len(parts) == 2:
                    out.append(parts[1].strip('"'))
        return out

    @staticmethod
    def load_all_accounts_folders(pracownik_id: int):
        accounts = email_actions.load_accounts(pracownik_id)
        out = []
        for kid, email_addr in accounts:
            folders = email_actions.load_folders(kid)
            out.append((kid, email_addr, folders))
        return out

    # ============================================================
    # LISTA WIADOMOŚCI
    # ============================================================
    @staticmethod
    def refresh_email_list(konto_id: int, folder: str):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT w.uid, w.od, w.temat, w.data, w.przeczytana,
                   p.imie || ' ' || p.nazwisko AS pracownik
            FROM wiadomosci w
            LEFT JOIN pracownicy p ON w.pracownik_id = p.id
            WHERE w.konto_id = ? AND w.folder = ?
            ORDER BY w.data DESC
        """, (konto_id, folder))
        rows = cur.fetchall()
        conn.close()
        return rows

    # ============================================================
    # WYSZUKIWARKA
    # ============================================================
    @staticmethod
    def search_messages(konto_id: int, folder: str, query: str):
        query = f"%{query.lower()}%"
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT w.uid, w.od, w.temat, w.data, w.przeczytana,
                   p.imie || ' ' || p.nazwisko AS pracownik
            FROM wiadomosci w
            LEFT JOIN pracownicy p ON w.pracownik_id = p.id
            WHERE w.konto_id = ? AND w.folder = ?
              AND (LOWER(w.od) LIKE ?
                   OR LOWER(w.temat) LIKE ?
                   OR LOWER(w.data) LIKE ?)
            ORDER BY w.data DESC
        """, (konto_id, folder, query, query, query))
        rows = cur.fetchall()
        conn.close()
        return rows

    # ============================================================
    # TREŚĆ WIADOMOŚCI
    # ============================================================
    @staticmethod
    def load_message(konto_id: int, folder: str, uid: str):
        return load_email_message(konto_id, folder, uid)

    @staticmethod
    def extract_html(msg):
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                raw = part.get_payload(decode=True)
                if raw:
                    try:
                        return raw.decode(part.get_content_charset() or "utf-8", errors="ignore")
                    except:
                        return raw.decode("utf-8", errors="ignore")
        return None

    # ============================================================
    # ZAŁĄCZNIKI
    # ============================================================
    @staticmethod
    def get_attachments(msg):
        return list_attachments(msg)

    @staticmethod
    def open_attachment(konto_id, folder, uid, part_index):
        raw = download_attachment(konto_id, folder, uid, part_index)
        if not raw:
            return None

        tmp_dir = tempfile.gettempdir()
        path = os.path.join(tmp_dir, f"mail_attachment_{uid}_{part_index}.pdf")

        with open(path, "wb") as f:
            f.write(raw)

        return path

    @staticmethod
    def download_all_attachments(konto_id, folder, uid, attachments):
        target_dir = QFileDialog.getExistingDirectory(None, "Wybierz folder do zapisania załączników")
        if not target_dir:
            return False

        for att in attachments:
            raw = download_attachment(konto_id, folder, uid, att["index"])
            if not raw:
                continue

            path = os.path.join(target_dir, att["filename"])
            with open(path, "wb") as f:
                f.write(raw)

        return True

    # ============================================================
    # PRZENOSZENIE / USUWANIE
    # ============================================================
    @staticmethod
    def move_email(konto_id: int, uid: str, target_folder: str):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT serwer_imap, port_imap, haslo_enc, email FROM konta_email WHERE id = ?", (konto_id,))
        row = cur.fetchone()
        conn.close()

        if not row:
            return False

        imap = EmailSyncer.connect_imap(row[1], row[2], row[0], row[3])
        if not imap:
            return False

        ok = move_email_to_folder(imap, uid, target_folder)
        imap.logout()
        return ok

    @staticmethod
    def move_email_and_get_sender(konto_id: int, uid: str, target_folder: str):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT od FROM wiadomosci WHERE konto_id = ? AND uid = ?", (konto_id, uid))
        row = cur.fetchone()
        conn.close()

        sender = row[0] if row else None

        ok = email_actions.move_email(konto_id, uid, target_folder)
        return ok, sender

    @staticmethod
    def delete_email(konto_id: int, uid: str):
        return email_actions.move_email(konto_id, uid, "Trash")

    # ============================================================
    # OZNACZANIE
    # ============================================================
    @staticmethod
    def mark_read(konto_id: int, uid: str):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE wiadomosci SET przeczytana = 1 WHERE uid = ? AND konto_id = ?", (uid, konto_id))
        conn.commit()
        conn.close()

    @staticmethod
    def mark_unread(konto_id: int, uid: str):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE wiadomosci SET przeczytana = 0 WHERE uid = ? AND konto_id = ?", (uid, konto_id))
        conn.commit()
        conn.close()

    @staticmethod
    def mark_spam(konto_id: int, uid: str):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE wiadomosci SET folder = 'Spam' WHERE uid = ? AND konto_id = ?", (uid, konto_id))
        conn.commit()
        conn.close()

    # ============================================================
    # FILTRY
    # ============================================================
    @staticmethod
    def add_filter(konto_id: int, sender: str, folder: str):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO reguly_filtrow (konto_id, od, temat_zawiera, folder_docelowy, aktywna)
            VALUES (?, ?, NULL, ?, 1)
        """, (konto_id, sender, folder))
        conn.commit()
        conn.close()


# ============================================================
#  SYNC WORKER (QThreadPool)
# ============================================================

class SyncWorkerSignals(QObject):
    finished = pyqtSignal(bool)
    error = pyqtSignal(str)


class SyncRunnable(QRunnable):
    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        self.signals = SyncWorkerSignals()
        self.setAutoDelete(True)

    def run(self):
        try:
            ok = email_actions.sync_all_emails(self.parent.pracownik_id)
            self.signals.finished.emit(ok)
        except Exception as e:
            self.signals.error.emit(str(e))
            self.signals.finished.emit(False)
