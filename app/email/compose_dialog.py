from __future__ import annotations

import os
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QFont, QTextCursor
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QComboBox,
    QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QTextEdit,
    QToolBar, QVBoxLayout,
)


def split_addresses(text: str) -> List[str]:
    return [a.strip() for a in (text or "").replace(";", ",").split(",") if a.strip()]


class ComposeDialog(QDialog):
    """Okno tworzenia wiadomości w stylu Thunderbirda (formatowanie, DW, UDW, załączniki)."""

    def __init__(
        self,
        parent=None,
        from_addr: str = "",
        to: str = "",
        cc: str = "",
        subject: str = "",
        body: str = "",
        attachments: Optional[List[str]] = None,
        title: str = "Nowa wiadomość",
        html: str = "",
        pracownik_id: Optional[int] = None,
        accounts: Optional[List[dict]] = None,
        account_id: Optional[int] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 640)
        self.pracownik_id = pracownik_id

        self.from_combo: Optional[QComboBox] = None
        if accounts and len(accounts) > 1:
            self.from_combo = QComboBox()
            for acc in accounts:
                name = acc.get("display_name") or ""
                label = f"{name} <{acc['email']}>" if name else acc["email"]
                self.from_combo.addItem(label, acc["id"])
            index = self.from_combo.findData(account_id)
            if index >= 0:
                self.from_combo.setCurrentIndex(index)
        elif accounts:
            account_id = accounts[0]["id"]
        self._account_id = account_id

        self.to_edit = QLineEdit(to)
        self.to_edit.setPlaceholderText("adres1@domena.pl, adres2@domena.pl")
        self.cc_edit = QLineEdit(cc)
        self.bcc_edit = QLineEdit()
        self.subject_edit = QLineEdit(subject)

        self.body_edit = QTextEdit()
        self.body_edit.setAcceptRichText(True)
        html = self._with_signature(html, body)
        if html:
            self.body_edit.setHtml(html)
        else:
            self.body_edit.setPlainText(body)

        self.attachments_list = QListWidget()
        self.attachments_list.setMaximumHeight(70)
        for path in attachments or []:
            self.attachments_list.addItem(path)

        form = QFormLayout()
        if self.from_combo:
            form.addRow("Nadawca:", self.from_combo)
        elif from_addr:
            form.addRow("Nadawca:", QLabel(from_addr))
        form.addRow("Do:", self.to_edit)
        form.addRow("Kopia:", self.cc_edit)
        form.addRow("Ukryta kopia:", self.bcc_edit)
        form.addRow("Temat:", self.subject_edit)

        btn_add = QPushButton("Załącz…")
        btn_add.clicked.connect(self._add_attachment)
        btn_remove = QPushButton("Usuń załącznik")
        btn_remove.clicked.connect(self._remove_attachment)
        att_buttons = QHBoxLayout()
        att_buttons.addWidget(btn_add)
        att_buttons.addWidget(btn_remove)
        if pracownik_id:
            btn_sig = QPushButton("Moja stopka…")
            btn_sig.clicked.connect(self._edit_signature)
            att_buttons.addWidget(btn_sig)
        att_buttons.addStretch()

        buttons = QDialogButtonBox()
        self.send_button = buttons.addButton("Wyślij", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Anuluj", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self._build_format_bar())
        layout.addWidget(self.body_edit, 1)
        layout.addWidget(QLabel("Załączniki:"))
        layout.addWidget(self.attachments_list)
        layout.addLayout(att_buttons)
        layout.addWidget(buttons)

        if not to:
            self.to_edit.setFocus()
        else:
            self.body_edit.setFocus()
            self.body_edit.moveCursor(QTextCursor.MoveOperation.Start)

    def _with_signature(self, html: str, body: str) -> str:
        if not self.pracownik_id:
            return html
        from app.email.signature import get_employee_signature, merge_signature

        sig_text, sig_html = get_employee_signature(self.pracownik_id)
        return merge_signature(html, body, sig_html, sig_text) or html

    def _edit_signature(self) -> None:
        from app.db.dao_employees import get_employee_info
        from app.email.signature_editor import SignatureDialog

        info = get_employee_info(self.pracownik_id) or {}
        SignatureDialog(
            self.pracownik_id, info.get("imie", ""), info.get("nazwisko", ""), self
        ).exec()

    def _build_format_bar(self) -> QToolBar:
        bar = QToolBar()

        def add(text: str, tip: str, callback, checkable: bool = False, font: Optional[QFont] = None):
            action = QAction(text, self)
            action.setToolTip(tip)
            action.setCheckable(checkable)
            if font:
                action.setFont(font)
            action.triggered.connect(callback)
            bar.addAction(action)

        bold = QFont()
        bold.setBold(True)
        italic = QFont()
        italic.setItalic(True)
        underline = QFont()
        underline.setUnderline(True)
        add("B", "Pogrubienie", self._toggle_bold, True, bold)
        add("I", "Kursywa", self._toggle_italic, True, italic)
        add("U", "Podkreślenie", self._toggle_underline, True, underline)
        bar.addSeparator()
        add("• Lista", "Lista punktowana", self._bullet_list)
        bar.addSeparator()
        add("⇤", "Do lewej", lambda: self.body_edit.setAlignment(Qt.AlignmentFlag.AlignLeft))
        add("⇔", "Do środka", lambda: self.body_edit.setAlignment(Qt.AlignmentFlag.AlignHCenter))
        add("⇥", "Do prawej", lambda: self.body_edit.setAlignment(Qt.AlignmentFlag.AlignRight))
        return bar

    def _toggle_bold(self, checked: bool) -> None:
        self.body_edit.setFontWeight(
            QFont.Weight.Bold if checked else QFont.Weight.Normal
        )

    def _toggle_italic(self, checked: bool) -> None:
        self.body_edit.setFontItalic(checked)

    def _toggle_underline(self, checked: bool) -> None:
        self.body_edit.setFontUnderline(checked)

    def _bullet_list(self) -> None:
        from PyQt6.QtGui import QTextListFormat

        self.body_edit.textCursor().createList(QTextListFormat.Style.ListDisc)

    def _add_attachment(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Wybierz załączniki")
        for path in paths:
            self.attachments_list.addItem(path)

    def _remove_attachment(self) -> None:
        for item in self.attachments_list.selectedItems():
            self.attachments_list.takeItem(self.attachments_list.row(item))

    def _accept(self) -> None:
        if not split_addresses(self.to_edit.text()):
            QMessageBox.warning(self, "Wiadomość", "Podaj co najmniej jednego odbiorcę.")
            return
        self.accept()

    def values(self) -> dict:
        return {
            "account_id": self.from_combo.currentData() if self.from_combo else self._account_id,
            "to": split_addresses(self.to_edit.text()),
            "cc": split_addresses(self.cc_edit.text()),
            "bcc": split_addresses(self.bcc_edit.text()),
            "subject": self.subject_edit.text().strip(),
            "body": self.body_edit.toPlainText(),
            "html": self.body_edit.toHtml(),
            "attachments": [
                self.attachments_list.item(i).text()
                for i in range(self.attachments_list.count())
                if os.path.isfile(self.attachments_list.item(i).text())
            ],
        }

