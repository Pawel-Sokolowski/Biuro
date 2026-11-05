"""Debounced shared-contact suggestions for one token of a recipient list."""
from PyQt6.QtCore import Qt, QTimer, QModelIndex
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QCompleter, QLineEdit

from app.db.dao_address_book import list_contacts
from app.email.address_book import contact_mailbox, contact_name, recipient_spans
from app.utils.background import TaskRunner


class RecipientEdit(QLineEdit):
    def __init__(self, actor_id, text='', parent=None):
        super().__init__(text, parent)
        self.actor_id = actor_id
        self._reads = TaskRunner(self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(180)
        self._timer.timeout.connect(self._suggest)
        self._model = QStandardItemModel(self)
        self._completer = QCompleter(self._model, self)
        self._completer.setWidget(self)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.activated[QModelIndex].connect(self._choose)
        self.textEdited.connect(self._queue)
        self.cursorPositionChanged.connect(lambda *_args: self._completer.popup().hide())

    def keyPressEvent(self, event):
        popup = self._completer.popup()
        if popup.isVisible():
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
                index = popup.currentIndex()
                if not index.isValid():
                    index = self._completer.completionModel().index(0, 0)
                self._choose(index)
                event.accept()
                return
            if event.key() == Qt.Key.Key_Escape:
                popup.hide()
                event.accept()
                return
        super().keyPressEvent(event)

    def _token(self):
        text, cursor = self.text(), self.cursorPosition()
        for start, end in recipient_spans(text):
            if start <= cursor <= end:
                while start < end and text[start].isspace():
                    start += 1
                while end > start and text[end - 1].isspace():
                    end -= 1
                return start, end
        return len(text), len(text)

    def _query(self):
        start, end = self._token()
        text = self.text()[start:end].strip().strip('"')
        return '' if '>' in text else text

    def _queue(self):
        self._reads.invalidate()
        self._completer.popup().hide()
        if self.actor_id and len(self._query()) >= 2:
            self._timer.start()
        else:
            self._timer.stop()
            self._completer.popup().hide()

    def _suggest(self):
        query = self._query()
        if not self.actor_id or len(query) < 2:
            return
        def loaded(contacts):
            if self._query() != query or not self.hasFocus():
                return
            self._model.clear()
            seen = set()
            for contact in contacts:
                for address in (contact['email'], contact.get('additional_email')):
                    if not address or address.casefold() in seen or ('@' in query and query.casefold() not in address.casefold()):
                        continue
                    seen.add(address.casefold())
                    label = f'{contact_name(contact)} — {address}'
                    if contact.get('company'):
                        label += f' · {contact["company"]}'
                    item = QStandardItem(label)
                    item.setData(contact_mailbox(contact, address), Qt.ItemDataRole.UserRole)
                    self._model.appendRow(item)
            self._completer.setCompletionPrefix('')
            if self._model.rowCount():
                self._completer.complete()
            else:
                self._completer.popup().hide()
        self._reads.submit(lambda: list_contacts(self.actor_id, query, 30), loaded,
                           lambda _exc: self._completer.popup().hide())

    def _choose(self, index):
        mailbox = index.data(Qt.ItemDataRole.UserRole)
        if not mailbox:
            return
        self._timer.stop()
        self._reads.invalidate()
        start, end = self._token()
        suffix = self.text()[end:]
        value = self.text()[:start] + mailbox
        cursor = len(value)
        if not suffix.strip():
            value += ', '
            cursor = len(value)
        else:
            value += suffix
        self.setText(value)
        self.setCursorPosition(cursor)
        self._completer.popup().hide()
