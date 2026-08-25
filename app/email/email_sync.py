# app/email/email_sync.py
import imaplib
import sqlite3
import datetime
import email
from email.header import decode_header

from app.db.dao_companies import get_conn
from app.utils.crypto import decrypt_password


# ============================================================
#  DEKODOWANIE NAGŁÓWKÓW
# ============================================================

def _decode_header_value(value):
    if not value:
        return None
    try:
        decoded_parts = decode_header(value)
        result = ""
        for part, enc in decoded_parts:
            if isinstance(part, bytes):
                result += part.decode(enc or "utf-8", errors="ignore")
            else:
                result += part
        return result
    except Exception:
        return value


# ============================================================
#  LISTA ZAŁĄCZNIKÓW
# ============================================================

def list_attachments(msg):
    attachments = []
    index = 1

    for part in msg.walk():
        content_disposition = part.get_content_disposition()
        filename = part.get_filename()

        if content_disposition == "attachment" and filename:
            payload = part.get_payload(decode=True) or b""
            attachments.append({
                "index": index,
                "filename": _decode_header_value(filename),
                "content_type": part.get_content_type(),
                "size": len(payload)
            })

        index += 1

    return attachments


# ============================================================
#  DOWNLOAD ATTACHMENT
# ============================================================

def download_attachment(konto_id, folder, email_uid, part_index):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT serwer_imap, port_imap, haslo_enc, email
            FROM konta_email WHERE id = ?
        """, (konto_id,))
        row = cur.fetchone()
        conn.close()

        if not row:
            return None

        imap_srv, imap_port, haslo_enc, email_addr = row
        password = decrypt_password(haslo_enc)

        imap = imaplib.IMAP4_SSL(imap_srv, imap_port)
        imap.login(email_addr, password)
        imap.select(folder or "INBOX")

        status, data = imap.uid("fetch", email_uid, f"BODY.PEEK[{part_index}]")
        if status != "OK" or not data or not data[0]:
            imap.logout()
            return None

        raw = data[0][1]
        imap.logout()
        return raw

    except Exception as e:
        print("[ATTACHMENT] ERROR:", e)
        return None


# ============================================================
#  MOVE EMAIL
# ============================================================

def move_email_to_folder(imap, email_uid, target_folder):
    try:
        imap.uid("COPY", email_uid, target_folder)
        imap.uid("STORE", email_uid, "+FLAGS", r"(\Deleted)")
        imap.expunge()
        return True
    except Exception as e:
        print("[MOVE] ERROR:", e)
        return False


# ============================================================
#  LOAD EMAIL MESSAGE (UID)
# ============================================================

def load_email_message(konto_id, folder, email_uid):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT serwer_imap, port_imap, haslo_enc, email
            FROM konta_email WHERE id = ?
        """, (konto_id,))
        row = cur.fetchone()
        conn.close()

        if not row:
            return None

        imap_srv, imap_port, haslo_enc, email_addr = row
        password = decrypt_password(haslo_enc)

        imap = imaplib.IMAP4_SSL(imap_srv, imap_port)
        imap.login(email_addr, password)
        imap.select(folder or "INBOX")

        status, data = imap.uid("fetch", email_uid, "(RFC822)")
        if status != "OK" or not data or not data[0]:
            imap.logout()
            return None

        raw = data[0][1]
        msg = email.message_from_bytes(raw)
        imap.logout()
        return msg

    except Exception as e:
        print("[LOAD] ERROR:", e)
        return None


# ============================================================
#  GŁÓWNA KLASA SYNC
# ============================================================

class EmailSyncer:

    @staticmethod
    def connect_imap(serwer_imap, port_imap, email_address, encrypted_password, timeout=30):
        try:
            password = decrypt_password(encrypted_password)
            server = imaplib.IMAP4_SSL(serwer_imap, int(port_imap), timeout=timeout)
            server.login(email_address, password)
            return server
        except Exception as e:
            print(f"[IMAP] Błąd połączenia: {e}")
            return None

    @staticmethod
    def sync_emails(konto_id, pracownik_id, max_messages=50):
        conn = get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT email, serwer_imap, port_imap, haslo_enc
                FROM konta_email WHERE id = ?
            """, (konto_id,))
            account = cursor.fetchone()

            if not account:
                print(f"[SYNC] Konto {konto_id} nie znalezione")
                return False

            email_addr, imap_server, imap_port, encrypted_pwd = account

            imap = EmailSyncer.connect_imap(imap_server, imap_port, email_addr, encrypted_pwd)
            if not imap:
                return False

            status, _ = imap.select("INBOX", readonly=True)
            if status != "OK":
                print("[SYNC] Nie można otworzyć INBOX")
                imap.logout()
                return False

            status, message_nums = imap.search(None, "ALL")
            if status != "OK" or not message_nums or not message_nums[0]:
                imap.logout()
                return True

            all_nums = message_nums[0].split()
            target_nums = all_nums[-max_messages:]

            for num in target_nums:
                try:
                    status, msg_data = imap.fetch(num, "(RFC822)")
                    if status != "OK" or not msg_data or not msg_data[0]:
                        continue

                    raw_bytes = msg_data[0][1]
                    msg = email.message_from_bytes(raw_bytes)

                    status_uid, uid_data = imap.fetch(num, "(UID)")
                    uid = None
                    if status_uid == "OK" and uid_data and uid_data[0]:
                        try:
                            uid = uid_data[0].decode().split("UID ")[1].split(" ")[0]
                        except Exception:
                            uid = None

                    message_id = _decode_header_value(msg.get("Message-ID")) or f"unknown_{num.decode()}_{datetime.datetime.now().timestamp()}"
                    od = _decode_header_value(msg.get("From"))
                    do = _decode_header_value(msg.get("To"))
                    cc = _decode_header_value(msg.get("Cc"))
                    temat = _decode_header_value(msg.get("Subject"))
                    data = _decode_header_value(msg.get("Date")) or datetime.datetime.now().isoformat()

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
                            for part in msg.walk():
                                if part.get_content_type() == "text/html":
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
                            (konto_id, uid, message_id, od, do, cc, temat, tresc, data, folder, raw_msg)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            konto_id,
                            uid,
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
                        pass

                except Exception as msg_err:
                    print(f"[SYNC] Pominięto wiadomość: {msg_err}")
                    continue

            try:
                imap.close()
            except Exception:
                pass
            imap.logout()
            conn.commit()
            return True

        except Exception as e:
            print(f"[SYNC] Błąd: {e}")
            return False

        finally:
            conn.close()


# ============================================================
#  MODULE WRAPPER
# ============================================================

def sync_emails(konto_id, pracownik_id, max_messages=50):
    try:
        return EmailSyncer.sync_emails(konto_id, pracownik_id, max_messages)
    except Exception as e:
        print("[SYNC WRAPPER] ERROR:", e)
        return False