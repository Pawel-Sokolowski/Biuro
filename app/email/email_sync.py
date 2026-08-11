# app/email/email_sync.py
import sqlite3
import imaplib
import smtplib
import email
import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import decode_header

from app.db.dao_companies import get_conn
from app.utils.crypto import decrypt_password

class EmailSyncer:
    """Klasa do synchronizacji emaili z serwera IMAP oraz wysyłania wiadomości."""

    @staticmethod
    def connect_imap(serwer_imap, port_imap, email_address, encrypted_password, timeout=30):
        """Połącz się z serwerem IMAP i zaloguj."""
        try:
            password = decrypt_password(encrypted_password)
            server = imaplib.IMAP4_SSL(serwer_imap, int(port_imap), timeout=timeout)
            server.login(email_address, password)
            return server
        except Exception as e:
            print(f"Błąd połączenia IMAP: {e}")
            return None

    @staticmethod
    def _decode_header_value(value):
        if not value:
            return ""
        parts = decode_header(value)
        decoded = ""
        for part, enc in parts:
            if isinstance(part, bytes):
                try:
                    decoded += part.decode(enc or "utf-8", errors="ignore")
                except Exception:
                    decoded += part.decode("utf-8", errors="ignore")
            else:
                decoded += str(part)
        return decoded

    @staticmethod
    def sync_emails(konto_id, pracownik_id, max_messages=50):
        """Synchronizuj emaile z IMAP do bazy (zapisuje raw_msg)."""
        conn = get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT email, serwer_imap, port_imap, haslo_enc
                FROM konta_email WHERE id = ?
            """, (konto_id,))
            account = cursor.fetchone()

            if not account:
                print(f"Konto email {konto_id} nie znalezione")
                return False

            email_addr, imap_server, imap_port, encrypted_pwd = account

            imap = EmailSyncer.connect_imap(imap_server, imap_port, email_addr, encrypted_pwd)
            if not imap:
                return False

            status, _ = imap.select("INBOX", readonly=True)
            if status != "OK":
                print("Nie udało się otworzyć folderu INBOX")
                imap.logout()
                return False

            status, message_nums = imap.search(None, "ALL")
            if status != "OK" or not message_nums or not message_nums[0]:
                imap.logout()
                return True

            all_nums = message_nums[0].split()
            # ogranicz do ostatnich max_messages
            target_nums = all_nums[-max_messages:]

            for num in target_nums:
                try:
                    status, msg_data = imap.fetch(num, "(RFC822)")
                    if status != "OK" or not msg_data or not msg_data[0]:
                        continue

                    raw_bytes = msg_data[0][1]
                    msg = email.message_from_bytes(raw_bytes)

                    message_id = EmailSyncer._decode_header_value(msg.get("Message-ID")) or f"unknown_{num.decode()}_{datetime.datetime.now().timestamp()}"
                    od = EmailSyncer._decode_header_value(msg.get("From"))
                    do = EmailSyncer._decode_header_value(msg.get("To"))
                    cc = EmailSyncer._decode_header_value(msg.get("Cc"))
                    temat = EmailSyncer._decode_header_value(msg.get("Subject"))
                    data = EmailSyncer._decode_header_value(msg.get("Date")) or datetime.datetime.now().isoformat()

                    # Treść: preferuj text/plain, fallback na text/html
                    tresc = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            ctype = part.get_content_type()
                            disp = str(part.get("Content-Disposition") or "")
                            if ctype == "text/plain" and "attachment" not in disp:
                                payload = part.get_payload(decode=True) or b""
                                tresc = payload.decode(part.get_content_charset() or "utf-8", errors="ignore")
                                break
                        if not tresc:
                            # spróbuj html
                            for part in msg.walk():
                                if part.get_content_type() == "text/html" and "attachment" not in str(part.get("Content-Disposition") or ""):
                                    payload = part.get_payload(decode=True) or b""
                                    tresc = payload.decode(part.get_content_charset() or "utf-8", errors="ignore")
                                    break
                    else:
                        payload = msg.get_payload(decode=True) or b""
                        tresc = payload.decode(msg.get_content_charset() or "utf-8", errors="ignore")

                    final_folder = "INBOX"
                    cursor.execute("""
                        SELECT folder_docelowy FROM reguly_filtrow
                        WHERE konto_id = ? AND aktywna = 1
                        AND (od IS NULL OR ? LIKE '%' || od || '%')
                        AND (temat_zawiera IS NULL OR ? LIKE '%' || temat_zawiera || '%')
                        LIMIT 1
                    """, (konto_id, od or "", temat or ""))
                    filter_result = cursor.fetchone()
                    if filter_result and filter_result[0]:
                        final_folder = filter_result[0]

                    try:
                        cursor.execute("""
                            INSERT INTO wiadomosci
                            (konto_id, message_id, od, do, cc, temat, tresc, data_otrzymania, folder, raw_msg)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            konto_id,
                            str(message_id),
                            str(od),
                            str(do),
                            str(cc),
                            str(temat)[:255],
                            str(tresc)[:4000],
                            str(data),
                            str(final_folder),
                            sqlite3.Binary(raw_bytes)
                        ))
                    except sqlite3.IntegrityError:
                        # już istnieje
                        pass
                except Exception as msg_err:
                    print(f"Pominięto pojedynczą wiadomość: {msg_err}")
                    continue

            try:
                imap.close()
            except Exception:
                pass
            imap.logout()
            conn.commit()
            return True

        except Exception as e:
            print(f"Błąd synchronizacji email: {e}")
            return False
        finally:
            conn.close()

    @staticmethod
    def send_email(konto_id, do, temat, tresc):
        """Wyślij email."""
        conn = get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT email, serwer_smtp, port_smtp, haslo_enc
                FROM konta_email WHERE id = ?
            """, (konto_id,))
            account = cursor.fetchone()

            if not account:
                return False, "Konto nie znalezione"

            email_addr, smtp_server, smtp_port, encrypted_pwd = account
            password = decrypt_password(encrypted_pwd)

            msg = MIMEMultipart()
            msg['From'] = email_addr
            msg['To'] = do
            msg['Subject'] = temat
            msg.attach(MIMEText(tresc, 'plain'))

            if int(smtp_port) == 465:
                with smtplib.SMTP_SSL(smtp_server, int(smtp_port)) as server:
                    server.login(email_addr, password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(smtp_server, int(smtp_port)) as server:
                    server.starttls()
                    server.login(email_addr, password)
                    server.send_message(msg)

            return True, "Wiadomość wysłana"

        except Exception as e:
            return False, f"Błąd wysyłania: {str(e)}"
        finally:
            conn.close()


def load_email_message(konto_id, folder, email_uid):
    """Pobierz pełną wiadomość RFC822 z serwera IMAP (z użyciem UID)."""
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT serwer_imap, port_imap, haslo_enc, email
            FROM konta_email WHERE id = ?
        """, (konto_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return None
        imap_srv, imap_port, haslo_enc, email_addr = row
        conn.close()

        password = decrypt_password(haslo_enc)
        imap = imaplib.IMAP4_SSL(imap_srv, imap_port, timeout=30)
        imap.login(email_addr, password)
        imap.select(folder or "INBOX")
        status, data = imap.uid('fetch', email_uid, '(RFC822)')
        if status != 'OK' or not data or not data[0]:
            imap.logout()
            return None
        raw = data[0][1]
        msg = email.message_from_bytes(raw)
        imap.logout()
        return msg
    except Exception:
        return None


def move_email_to_folder(imap, email_uid, target_folder):
    """Przenieś wiadomość na serwerze IMAP."""
    try:
        imap.uid("COPY", email_uid, target_folder)
        imap.uid("STORE", email_uid, "+FLAGS", r"(\Deleted)")
        imap.expunge()
        return True
    except Exception:
        return False


def list_attachments(msg):
    """Zwraca listę załączników z wiadomości email.message.Message."""
    attachments = []
    index = 1

    for part in msg.walk():
        content_disposition = part.get_content_disposition()
        filename = part.get_filename()

        if content_disposition == "attachment" and filename:
            payload = part.get_payload(decode=True) or b""
            attachments.append({
                "index": index,
                "filename": filename,
                "content_type": part.get_content_type(),
                "size": len(payload)
            })

        index += 1

    return attachments


def download_attachment(konto_id, folder, email_uid, part_index):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT serwer_imap, port_imap, haslo_enc, email
        FROM konta_email WHERE id = ?
    """, (konto_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    imap_srv, imap_port, haslo_enc, email_addr = row
    conn.close()

    password = decrypt_password(haslo_enc)
    imap = imaplib.IMAP4_SSL(imap_srv, imap_port)
    imap.login(email_addr, password)
    imap.select(folder or "INBOX")

    # Pobieramy BODY.PEEK dla konkretnej części (part_index)
    status, data = imap.uid("fetch", email_uid, f"BODY.PEEK[{part_index}]")
    if status != "OK" or not data or not data[0]:
        imap.logout()
        return None

    raw = data[0][1]
    imap.logout()
    return raw
