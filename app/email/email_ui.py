# app/email/email_page.py

from __future__ import annotations

import imaplib
import email
import email.header
from typing import List, Dict, Any, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QTreeWidget, QTreeWidgetItem, QMessageBox, QSplitter, QPushButton
)
from PyQt6.QtWidgets import QTextBrowser
from PyQt6.QtCore import Qt

from app.email.email_storage import (
    get_email_accounts_for_employee,
    get_folders_for_account,
    add_folder_for_account,
    remove_folder_for_account
)
from app.utils.crypto import decrypt_password

# NOWY SYNC — ZERO PARSOWANIA
from app.email.email_sync import (
    extract_folder_name,
    safe_header,
    sync_folders
)


class EmailPage(QWidget):
    """
    Główna strona mailowa — IMAP-only.
    Układ jak w Thunderbirdzie:
    - pasek narzędzi (synchronizacja)
    - lewa kolumna: foldery
    - środkowa kolumna: lista wiadomości
    - prawa kolumna: podgląd treści + załączniki
    """

    def __init__(self, pracownik_id: int):
        super().__init__()

        self.pracownik_id = pracownik_id
        self.accounts = get_email_accounts_for_employee(pracownik_id)

        self.current_account: Optional[Dict[str, Any]] = None
        self.current_folder_server: Optional[str] = None
        self.current_uid: Optional[str] = None

        # -----------------------------
        # GŁÓWNY LAYOUT
        # -----------------------------
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        # -----------------------------
        # PASEK NARZĘDZI
        # -----------------------------
        toolbar = QHBoxLayout()

        btn_sync = QPushButton("Synchronizacja wiadomości")
        btn_sync.clicked.connect(self.on_manual_sync)
        toolbar.addWidget(btn_sync)

        toolbar.addStretch()

        main_layout.addLayout(toolbar)

        # -----------------------------
        # SPLITTER — 3 kolumny
        # -----------------------------
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # LEWA: FOLDERY
        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderLabel("Foldery")
        self.folder_tree.itemClicked.connect(self.on_folder_clicked)
        splitter.addWidget(self.folder_tree)

        # ŚRODKOWA: LISTA WIADOMOŚCI
        self.mail_list = QListWidget()
        self.mail_list.itemClicked.connect(self.on_mail_clicked)
        splitter.addWidget(self.mail_list)

        # PRAWA: PODGLĄD TREŚCI
        self.mail_view = QTextBrowser()
        self.mail_view.setOpenLinks(False)
        self.mail_view.anchorClicked.connect(self.on_attachment_clicked)
        splitter.addWidget(self.mail_view)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 3)

        # -----------------------------
        # ŁADOWANIE KONTA
        # -----------------------------
        if self.accounts:
            self.current_account = self.accounts[0]

            # automatyczna synchronizacja folderów
            self.synchronize_folders()

            # załaduj foldery do UI
            self.load_folders_from_imap()
        else:
            QMessageBox.warning(self, "Brak kont", "Nie znaleziono żadnych kont email.")


    # =====================================================================
    # IMAP LOGIN
    # =====================================================================

    def open_imap(self) -> Optional[imaplib.IMAP4_SSL]:
        if not self.current_account:
            return None

        email_addr = self.current_account.get("email")
        host = self.current_account.get("serwer_imap")
        port = int(self.current_account.get("port_imap") or 993)
        enc = self.current_account.get("haslo_enc")

        try:
            pwd = decrypt_password(enc)
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie można odszyfrować hasła:\n{e}")
            return None

        try:
            imap = imaplib.IMAP4_SSL(host, port)
            imap.login(email_addr, pwd)
            return imap
        except Exception as e:
            QMessageBox.critical(self, "Błąd IMAP", f"Nie można połączyć z serwerem:\n{e}")
            return None


    # =====================================================================
    # BEZPIECZNY SELECT
    # =====================================================================

    def safe_select(self, imap) -> bool:
        """
        Stabilny select folderu IMAP — 3 próby:
        readonly=True, readonly=False, bez readonly.
        """
        for args in [
            (self.current_folder_server, True),
            (self.current_folder_server, False),
            (self.current_folder_server, None)
        ]:
            folder, readonly = args
            try:
                if readonly is None:
                    status, _ = imap.select(folder)
                else:
                    status, _ = imap.select(folder, readonly=readonly)

                if status == "OK":
                    return True
            except:
                pass

        return False

    def load_folders_from_imap(self) -> None:
        """
        Pobiera foldery IMAP i buduje PRAWDZIWE drzewo folderów.
        Zero parsowania — nazwy 1:1 jak na serwerze.
        """
        self.folder_tree.clear()

        imap = self.open_imap()
        if not imap:
            return

        mailboxes = []

        # stabilne próby LIST
        for args in [
            ("INBOX", "*"),
            ("INBOX", "%"),
            ("", "*"),
            ("", "%"),
        ]:
            try:
                typ, boxes = imap.list(*args)
                if typ == "OK" and boxes:
                    mailboxes = boxes
                    break
            except:
                pass

        if not mailboxes:
            QMessageBox.warning(self, "IMAP", "Serwer nie zwrócił żadnych folderów.")
            return

        # root node = email użytkownika
        root = QTreeWidgetItem([self.current_account["email"]])
        self.folder_tree.addTopLevelItem(root)

        # mapa: pełna ścieżka → QTreeWidgetItem
        nodes = {}

        for raw in mailboxes:
            folder_server = raw.decode().split('"')[-2] if '"' in raw.decode() else raw.decode().split()[-1]
            parts = folder_server.split(".")

            parent = root
            path = ""

            for part in parts:
                part = part.strip()
                if not part:
                    continue

                # budujemy ścieżkę
                path = path + "." + part if path else part

                # jeśli węzeł nie istnieje — tworzymy
                if path not in nodes:
                    item = QTreeWidgetItem([part])
                    item.setData(0, Qt.ItemDataRole.UserRole, path)
                    parent.addChild(item)
                    nodes[path] = item

                # schodzimy poziom niżej
                parent = nodes[path]

        self.folder_tree.expandAll()

        try:
            imap.logout()
        except:
            pass

    # =====================================================================
    # KLIK FOLDERU
    # =====================================================================

    def on_folder_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        server_name = item.data(0, Qt.ItemDataRole.UserRole)
        if not server_name:
            return

        self.current_folder_server = server_name
        self.load_mail_list_from_imap()

    # =====================================================================
    # LISTA WIADOMOŚCI
    # =====================================================================

    def load_mail_list_from_imap(self) -> None:
        """
        Pobiera listę wiadomości z IMAP dla aktualnego folderu
        i wyświetla je w QListWidget.
        """
        self.mail_list.clear()
        self.mail_view.clear()

        if not self.current_folder_server:
            return

        imap = self.open_imap()
        if not imap:
            return

        # BEZPIECZNY SELECT
        if not self.safe_select(imap):
            QMessageBox.warning(self, "IMAP",
                                f"Nie można wybrać folderu:\n{self.current_folder_server}")
            return

        try:
            typ, data = imap.search(None, "ALL")
            if typ != "OK":
                QMessageBox.warning(self, "IMAP", "Nie można pobrać listy wiadomości.")
                return

            uids = data[0].split()
            uids = uids[-300:]  # ostatnie 300 wiadomości

            for uid in reversed(uids):  # najnowsze na górze
                uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)

                typ, msg_data = imap.fetch(uid, "(BODY.PEEK[HEADER])")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue

                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                sender = safe_header(msg.get("From", ""))
                subject = safe_header(msg.get("Subject", ""))
                date = safe_header(msg.get("Date", ""))

                item_text = f"{sender}\n{subject}\n{date}"

                item = QListWidgetItem(item_text)
                item.setData(Qt.ItemDataRole.UserRole, uid_str)

                self.mail_list.addItem(item)

        except Exception as e:
            QMessageBox.critical(self, "IMAP", f"Nie można pobrać wiadomości:\n{e}")

        finally:
            try:
                imap.logout()
            except:
                pass

    # =====================================================================
    # KLIKNIĘCIE WIADOMOŚCI — POBIERANIE TREŚCI
    # =====================================================================

    def on_mail_clicked(self, item: QListWidgetItem) -> None:
        """
        Po kliknięciu wiadomości pobieramy pełną treść z IMAP + załączniki.
        """
        uid = item.data(Qt.ItemDataRole.UserRole)
        self.current_uid = uid

        if not uid:
            return

        imap = self.open_imap()
        if not imap:
            return

        # BEZPIECZNY SELECT
        if not self.safe_select(imap):
            QMessageBox.warning(self, "IMAP",
                                f"Nie można wybrać folderu:\n{self.current_folder_server}")
            return

        try:
            typ, msg_data = imap.fetch(uid, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                QMessageBox.warning(self, "IMAP", "Nie można pobrać treści wiadomości.")
                return

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            html = self.extract_html(msg)
            if html:
                self.mail_view.setHtml(html)
            else:
                text = self.extract_text(msg)
                self.mail_view.setPlainText(text)

            # załączniki
            attachments = self.get_attachments(msg)
            self.show_attachments(attachments)

        except Exception as e:
            QMessageBox.critical(self, "IMAP", f"Nie można pobrać treści wiadomości:\n{e}")

        finally:
            try:
                imap.logout()
            except:
                pass

    # =====================================================================
    # EKSTRAKCJA HTML / TEXT
    # =====================================================================

    def extract_html(self, msg: email.message.Message) -> Optional[str]:
        """
        Zwraca treść HTML z wiadomości.
        """
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    try:
                        return part.get_payload(decode=True).decode(
                            part.get_content_charset() or "utf-8",
                            errors="replace"
                        )
                    except Exception:
                        return None
        else:
            if msg.get_content_type() == "text/html":
                try:
                    return msg.get_payload(decode=True).decode(
                        msg.get_content_charset() or "utf-8",
                        errors="replace"
                    )
                except Exception:
                    return None
        return None

    def extract_text(self, msg: email.message.Message) -> str:
        """
        Zwraca treść tekstową wiadomości.
        """
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    try:
                        return part.get_payload(decode=True).decode(
                            part.get_content_charset() or "utf-8",
                            errors="replace"
                        )
                    except Exception:
                        return ""
        else:
            if msg.get_content_type() == "text/plain":
                try:
                    return msg.get_payload(decode=True).decode(
                        msg.get_content_charset() or "utf-8",
                        errors="replace"
                    )
                except Exception:
                    return ""
        return ""
    # =====================================================================
    # ZAŁĄCZNIKI — WYKRYWANIE
    # =====================================================================

    def get_attachments(self, msg: email.message.Message) -> List[Dict[str, Any]]:
        """
        Zwraca listę załączników:
        [
            {"index": 0, "filename": "...", "part": <email part>},
            ...
        ]
        """
        attachments = []
        idx = 0

        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue

            cd = part.get("Content-Disposition")
            if cd is None:
                continue

            if "attachment" in cd.lower() or "inline" in cd.lower():
                filename = part.get_filename()
                if filename:
                    attachments.append({
                        "index": idx,
                        "filename": safe_header(filename),
                        "part": part
                    })
                    idx += 1

        return attachments

    # =====================================================================
    # ZAŁĄCZNIKI — WYŚWIETLANIE LISTY
    # =====================================================================

    def show_attachments(self, attachments: List[Dict[str, Any]]) -> None:
        """
        Wyświetla listę załączników pod treścią wiadomości.
        """
        if not attachments:
            self.mail_view.append("\n\nBrak załączników.")
            return

        self.mail_view.append("\n\n<b>Załączniki:</b>")

        for att in attachments:
            fname = att["filename"]
            idx = att["index"]
            self.mail_view.append(f'<a href="attachment:{idx}">{fname}</a>')

    # =====================================================================
    # ZAŁĄCZNIKI — POBIERANIE NA DYSK
    # =====================================================================

    def on_attachment_clicked(self, url) -> None:
        """
        Kliknięcie załącznika — zapis na dysk.
        """
        href = url.toString()
        if not href.startswith("attachment:"):
            return

        idx = int(href.split(":")[1])

        # ponownie pobieramy wiadomość z IMAP
        imap = self.open_imap()
        if not imap:
            return

        # BEZPIECZNY SELECT
        if not self.safe_select(imap):
            QMessageBox.warning(self, "IMAP",
                                f"Nie można wybrać folderu:\n{self.current_folder_server}")
            return

        try:
            typ, msg_data = imap.fetch(self.current_uid, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                QMessageBox.warning(self, "Błąd", "Nie można pobrać wiadomości.")
                return

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            attachments = self.get_attachments(msg)
            if idx >= len(attachments):
                QMessageBox.warning(self, "Błąd", "Załącznik nie istnieje.")
                return

            part = attachments[idx]["part"]
            filename = attachments[idx]["filename"]

            data = part.get_payload(decode=True)
            if not data:
                QMessageBox.warning(self, "Błąd", "Nie można pobrać danych załącznika.")
                return

            import os
            save_dir = os.path.join(os.getcwd(), "attachments")
            os.makedirs(save_dir, exist_ok=True)

            path = os.path.join(save_dir, filename)

            with open(path, "wb") as f:
                f.write(data)

            QMessageBox.information(self, "Załącznik zapisany",
                                    f"Załącznik zapisano w:\n{path}")

        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie można pobrać załącznika:\n{e}")

        finally:
            try:
                imap.logout()
            except:
                pass

    # =====================================================================
    # OZNACZANIE WIADOMOŚCI JAKO PRZECZYTANE / NIEPRZECZYTANE
    # =====================================================================

    def mark_as_read(self, uid: str) -> None:
        imap = self.open_imap()
        if not imap:
            return

        if not self.safe_select(imap):
            QMessageBox.warning(self, "IMAP",
                                f"Nie można wybrać folderu:\n{self.current_folder_server}")
            return

        try:
            imap.store(uid, "+FLAGS", r"(\Seen)")
        except Exception as e:
            QMessageBox.warning(self, "Błąd", f"Nie można oznaczyć jako przeczytane:\n{e}")
        finally:
            try:
                imap.logout()
            except:
                pass

    def mark_as_unread(self, uid: str) -> None:
        imap = self.open_imap()
        if not imap:
            return

        if not self.safe_select(imap):
            QMessageBox.warning(self, "IMAP",
                                f"Nie można wybrać folderu:\n{self.current_folder_server}")
            return

        try:
            imap.store(uid, "-FLAGS", r"(\Seen)")
        except Exception as e:
            QMessageBox.warning(self, "Błąd", f"Nie można oznaczyć jako nieprzeczytane:\n{e}")
        finally:
            try:
                imap.logout()
            except:
                pass

    # =====================================================================
    # PRZENOSZENIE WIADOMOŚCI MIĘDZY FOLDERAMI
    # =====================================================================

    def move_message(self, uid: str, target_folder_server: str) -> None:
        """
        Przenosi wiadomość na serwerze IMAP:
        COPY → DELETE → EXPUNGE
        """
        imap = self.open_imap()
        if not imap:
            return

        if not self.safe_select(imap):
            QMessageBox.warning(self, "IMAP",
                                f"Nie można wybrać folderu:\n{self.current_folder_server}")
            return

        try:
            typ, _ = imap.copy(uid, target_folder_server)
            if typ != "OK":
                QMessageBox.warning(self, "Błąd", "Nie można skopiować wiadomości.")
                return

            imap.store(uid, "+FLAGS", r"(\Deleted)")
            imap.expunge()

            QMessageBox.information(self, "OK", "Wiadomość przeniesiona.")

            self.load_mail_list_from_imap()

        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie można przenieść wiadomości:\n{e}")

        finally:
            try:
                imap.logout()
            except:
                pass
    # =====================================================================
    # TWORZENIE FOLDERÓW
    # =====================================================================

    def create_folder(self, name: str, parent_server: Optional[str] = None) -> None:
        """
        Tworzy folder na serwerze IMAP.
        """
        imap = self.open_imap()
        if not imap:
            return

        server_name = name if not parent_server else f"{parent_server}.{name}"

        try:
            typ, _ = imap.create(server_name)
            if typ == "OK":
                QMessageBox.information(self, "OK", f"Folder '{server_name}' utworzony.")
                self.load_folders_from_imap()
            else:
                QMessageBox.warning(self, "Błąd", "Nie można utworzyć folderu.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie można utworzyć folderu:\n{e}")
        finally:
            try:
                imap.logout()
            except:
                pass

    # =====================================================================
    # USUWANIE FOLDERÓW
    # =====================================================================

    def delete_folder(self, server_name: str) -> None:
        """
        Usuwa folder na serwerze IMAP.
        """
        imap = self.open_imap()
        if not imap:
            return

        try:
            typ, _ = imap.delete(server_name)
            if typ == "OK":
                QMessageBox.information(self, "OK", f"Folder '{server_name}' usunięty.")
                self.load_folders_from_imap()
            else:
                QMessageBox.warning(self, "Błąd", "Nie można usunąć folderu.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie można usunąć folderu:\n{e}")
        finally:
            try:
                imap.logout()
            except:
                pass

    # =====================================================================
    # FOLDERY — DB
    # =====================================================================

    def get_folders_from_db(self) -> List[str]:
        return get_folders_for_account(self.current_account["id"])

    def add_folder_to_db(self, folder: str) -> None:
        add_folder_for_account(self.current_account["id"], folder)

    def remove_folder_from_db(self, folder: str) -> None:
        remove_folder_for_account(self.current_account["id"], folder)

    # =====================================================================
    # SYNCHRONIZACJA FOLDERÓW (AUTOMATYCZNA)
    # =====================================================================

    def synchronize_folders(self) -> None:
        """
        Automatyczna synchronizacja folderów IMAP z bazą danych.
        """
        imap = self.open_imap()
        if not imap:
            return

        db_folders = self.get_folders_from_db()

        # NOWY SYNC — ZERO PARSOWANIA
        imap_folders, new_folders, deleted_folders = sync_folders(imap, db_folders)

        for f in new_folders:
            self.add_folder_to_db(f)

        for f in deleted_folders:
            self.remove_folder_from_db(f)

        try:
            imap.logout()
        except:
            pass

    # =====================================================================
    # RĘCZNA SYNCHRONIZACJA (PRZYCISK)
    # =====================================================================

    def on_manual_sync(self) -> None:
        """
        Ręczna synchronizacja folderów + odświeżenie widoków.
        """
        self.synchronize_folders()
        self.load_folders_from_imap()

        if self.current_folder_server:
            self.load_mail_list_from_imap()

        QMessageBox.information(self, "Synchronizacja", "Synchronizacja zakończona.")

    # =====================================================================
    # ODŚWIEŻANIE WIDOKÓW
    # =====================================================================

    def refresh_all(self) -> None:
        """
        Odświeża foldery i listę wiadomości.
        """
        self.load_folders_from_imap()

        if self.current_folder_server:
            self.load_mail_list_from_imap()
