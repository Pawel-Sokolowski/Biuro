from PyQt6.QtCore import QRunnable, QObject, pyqtSignal
import imaplib
from email import message_from_bytes

from app.db.dao_companies import get_conn
from app.utils.crypto import decrypt_password
from app.email.email_sync import sync_emails, load_email_message, list_attachments, download_attachment, move_email_to_folder


# ============================================================
# SYGNAŁY WORKERA
# ============================================================

class SyncWorkerSignals(QObject):
    finished = pyqtSignal(bool)
    error = pyqtSignal(str)
    progress = pyqtSignal(int)


# ============================================================
# WORKER SYNC
# ============================================================

class SyncRunnable(QRunnable):
    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        self.signals = SyncWorkerSignals()
        self.setAutoDelete(True)

    def run(self):
        try:
            result = email_actions.sync_all_emails(self.parent.pracownik_id)
            self.signals.finished.emit(bool(result))
        except Exception as e:
            self.signals.error.emit(str(e))
            self.signals.finished.emit(False)


# ============================================================
# GŁÓWNY MODUŁ LOGIKI EMAIL
# ============================================================

class email_actions:

    # ------------------------------------------------------------
    # SYNC
    # ------------------------------------------------------------
    @staticmethod
    def sync_all_emails(pracownik_id: int):
        return sync_emails(pracownik_id)

    # ------------------------------------------------------------
    # ŁADOWANIE KONT
    # ------------------------------------------------------------
    @staticmethod
    def load_accounts(pracownik_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, email FROM konta_email WHERE pracownik_id = ?", (pracownik_id,))
        rows = cur.fetchall()
        conn.close()
        return rows

    # ------------------------------------------------------------
    # ŁADOWANIE FOLDERÓW IMAP
    # ------------------------------------------------------------
    @staticmethod
    def load_folders(konto_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT serwer_imap, port_imap, haslo_enc, email FROM konta_email WHERE id = ?", (konto_id,))
        row = cur.fetchone()
        conn.close()

        if not row:
            return []

        imap_srv, imap_port, haslo_enc, email_addr = row
        password = decrypt_password(haslo_enc)

        try:
            imap = imaplib.IMAP4_SSL(imap_srv, imap_port)
            imap.login(email_addr, password)
            status, folders = imap.list()
            imap.logout()

            if status != "OK":
                return []

            out = []
            for f in folders:
                parts = f.decode().split(' "/" ')
                if len(parts) == 2:
                    out.append(parts[1].strip('"'))
            return out

        except Exception:
            return []

    # ------------------------------------------------------------
    # LISTA WIADOMOŚCI
    # ------------------------------------------------------------
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

    # ------------------------------------------------------------
    # POBIERANIE TREŚCI
    # ------------------------------------------------------------
    @staticmethod
    def load_message(konto_id: int, folder: str, uid: str):
        return load_email_message(konto_id, folder, uid)

    # ------------------------------------------------------------
    # ZAŁĄCZNIKI
    # ------------------------------------------------------------
    @staticmethod
    def list_attachments(msg):
        return list_attachments(msg)

    @staticmethod
    def download_attachment(konto_id, folder, uid, part_index):
        return download_attachment(konto_id, folder, uid, part_index)

    # ------------------------------------------------------------
    # PRZENOSZENIE
    # ------------------------------------------------------------
    @staticmethod
    def move_email(konto_id: int, uid: str, target_folder: str):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT serwer_imap, port_imap, haslo_enc, email FROM konta_email WHERE id = ?", (konto_id,))
        row = cur.fetchone()
        conn.close()

        if not row:
            return False

        imap_srv, imap_port, haslo_enc, email_addr = row
        password = decrypt_password(haslo_enc)

        imap = imaplib.IMAP4_SSL(imap_srv, imap_port)
        imap.login(email_addr, password)
        ok = move_email_to_folder(imap, uid, target_folder)
        imap.logout()

        return ok

    # ------------------------------------------------------------
    # USUWANIE
    # ------------------------------------------------------------
    @staticmethod
    def delete_email(konto_id: int, uid: str):
        return email_actions.move_email(konto_id, uid, "Trash")

    # ------------------------------------------------------------
    # OZNACZANIE
    # ------------------------------------------------------------
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

    # ------------------------------------------------------------
    # ŹRÓDŁO WIADOMOŚCI
    # ------------------------------------------------------------
    @staticmethod
    def get_source(konto_id: int, folder: str, uid: str):
        msg = load_email_message(konto_id, folder, uid)
        if not msg:
            return None
        return msg.as_string()

    # ------------------------------------------------------------
    # ODPOWIEDŹ / FORWARD (placeholder)
    # ------------------------------------------------------------
    @staticmethod
    def reply(uid, konto_id, folder):
        return "TODO: reply"

    @staticmethod
    def forward(uid, konto_id, folder):
        return "TODO: forward"
