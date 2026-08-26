import os
import tempfile
from PyQt6.QtWidgets import QFileDialog
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
    # SYNC — synchronizacja wszystkich kont użytkownika
    # ============================================================
    @staticmethod
    def sync_all_emails(pracownik_id: int):
        accounts = email_actions.load_accounts(pracownik_id)
        ok = True
        for konto_id, _email in accounts:
            if not EmailSyncer.sync_emails(konto_id, pracownik_id):
                ok = False
        return ok

    # ============================================================
    # KONT — pobieranie kont przypisanych do użytkownika
    # ============================================================
    @staticmethod
    def load_accounts(pracownik_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT ke.id, ke.email
            FROM konta_email ke
            JOIN pracownik_konto_email pke ON pke.konto_id = ke.id
            WHERE pke.pracownik_id = ?
            ORDER BY ke.email
        """, (pracownik_id,))
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
        cur.execute("""
            SELECT serwer_imap, port_imap, haslo_enc, email, ssl_imap
            FROM konta_email
            WHERE id = ?
        """, (konto_id,))
        row = cur.fetchone()
        conn.close()

        if not row:
            return []

        serwer_imap = row["serwer_imap"]
        port_imap = row["port_imap"]
        haslo = row["haslo_enc"]
        email_addr = row["email"]
        ssl_mode = row["ssl_imap"]

        imap = EmailSyncer.connect_imap(
            serwer_imap,
            port_imap,
            email_addr,
            haslo,
            ssl_mode
        )

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
    def refresh_email_list(konto_id: int, folder_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT w.uid, w.nadawca AS od, w.temat, w.data, w.przeczytana,
                   p.imie || ' ' || p.nazwisko AS pracownik
            FROM wiadomosci_email w
            LEFT JOIN pracownicy p ON w.pracownik_id = p.id
            WHERE w.konto_id = ? AND w.folder_id = ?
            ORDER BY w.data DESC
        """, (konto_id, folder_id))
        rows = cur.fetchall()
        conn.close()
        return rows

    # ============================================================
    # WYSZUKIWARKA
    # ============================================================
    @staticmethod
    def search_messages(konto_id: int, folder_id: int, query: str):
        query = f"%{query.lower()}%"
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT w.uid, w.nadawca AS od, w.temat, w.data, w.przeczytana,
                   p.imie || ' ' || p.nazwisko AS pracownik
            FROM wiadomosci_email w
            LEFT JOIN pracownicy p ON w.pracownik_id = p.id
            WHERE w.konto_id = ? AND w.folder_id = ?
              AND (LOWER(w.nadawca) LIKE ?
                   OR LOWER(w.temat) LIKE ?
                   OR LOWER(w.data) LIKE ?)
            ORDER BY w.data DESC
        """, (konto_id, folder_id, query, query, query))
        rows = cur.fetchall()
        conn.close()
        return rows

    # ============================================================
    # TREŚĆ WIADOMOŚCI
    # ============================================================
    @staticmethod
    def load_message(konto_id: int, folder_id: int, uid: str):
        return load_email_message(konto_id, folder_id, uid)

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
    def open_attachment(konto_id, folder_id, uid, part_index):
        raw = download_attachment(konto_id, folder_id, uid, part_index)
        if not raw:
            return None

        tmp_dir = tempfile.gettempdir()
        path = os.path.join(tmp_dir, f"mail_attachment_{uid}_{part_index}.pdf")

        with open(path, "wb") as f:
            f.write(raw)

        return path

    @staticmethod
    def download_all_attachments(konto_id, folder_id, uid, attachments):
        target_dir = QFileDialog.getExistingDirectory(None, "Wybierz folder do zapisania załączników")
        if not target_dir:
            return False

        for att in attachments:
            raw = download_attachment(konto_id, folder_id, uid, att["index"])
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
    def move_email(konto_id: int, uid: str, target_folder_id: int):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT serwer_imap, port_imap, haslo_enc, email, ssl_imap
            FROM konta_email WHERE id = ?
        """, (konto_id,))
        row = cur.fetchone()
        conn.close()

        if not row:
            return False

        imap = EmailSyncer.connect_imap(
            row["serwer_imap"],
            row["port_imap"],
            row["email"],
            row["haslo_enc"],
            row["ssl_imap"]
        )

        if not imap:
            return False

        ok = move_email_to_folder(imap, uid, target_folder_id)
        imap.logout()
        return ok

    @staticmethod
    def delete_email(konto_id: int, uid: str):
        # folder "Trash" musi być ID folderu, nie tekstem
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id FROM foldery_email
            WHERE konto_id = ? AND LOWER(nazwa) = 'trash'
        """, (konto_id,))
        row = cur.fetchone()
        conn.close()

        if not row:
            return False

        trash_id = row["id"]
        return email_actions.move_email(konto_id, uid, trash_id)

    # ============================================================
    # OZNACZANIE
    # ============================================================
    @staticmethod
    def mark_read(konto_id: int, uid: str):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            UPDATE wiadomosci_email
            SET przeczytana = 1
            WHERE uid = ? AND konto_id = ?
        """, (uid, konto_id))
        conn.commit()
        conn.close()

    @staticmethod
    def mark_unread(konto_id: int, uid: str):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            UPDATE wiadomosci_email
            SET przeczytana = 0
            WHERE uid = ? AND konto_id = ?
        """, (uid, konto_id))
        conn.commit()
        conn.close()

    @staticmethod
    def mark_spam(konto_id: int, uid: str):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id FROM foldery_email
            WHERE konto_id = ? AND LOWER(nazwa) = 'spam'
        """, (konto_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return False

        spam_id = row["id"]

        cur.execute("""
            UPDATE wiadomosci_email
            SET folder_id = ?
            WHERE uid = ? AND konto_id = ?
        """, (spam_id, uid, konto_id))
        conn.commit()
        conn.close()
        return True
