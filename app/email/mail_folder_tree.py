"""Drzewo folderów poczty: przyjmuje upuszczone wiadomości i foldery."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QAbstractItemView, QTreeWidget, QWidget

from app.email.mail_constants import ROLE_FOLDER


class FolderTree(QTreeWidget):
    """Drzewo folderów przyjmujące upuszczone wiadomości i foldery (przenoszenie obsługuje strona)."""

    mails_dropped = pyqtSignal(object)
    folder_dropped = pyqtSignal(object, object)

    def __init__(self, mail_source: QWidget, parent=None):
        super().__init__(parent)
        self.mail_source = mail_source
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def _accepts(self, event) -> bool:
        return event.source() in (self, self.mail_source)

    def dragEnterEvent(self, event) -> None:
        if self._accepts(event):
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        item = self.itemAt(event.position().toPoint())
        if self._accepts(event) and item is not None and item.data(0, ROLE_FOLDER):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        item = self.itemAt(event.position().toPoint())
        if item is None or not item.data(0, ROLE_FOLDER) or not self._accepts(event):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        if event.source() is self:
            dragged = self.currentItem()
            if dragged is not None:
                self.folder_dropped.emit(dragged, item)
        else:
            self.mails_dropped.emit(item)
