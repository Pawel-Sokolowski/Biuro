from __future__ import annotations

from PyQt6.QtCore import QModelIndex, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import (
    QStyledItemDelegate,
    QApplication,
    QStyleOptionButton,
    QStyle,
)


class ButtonDelegate(QStyledItemDelegate):
    def __init__(self, parent=None, label: str = "", role: str = ""):
        super().__init__(parent)
        self._label = label
        self._role = role

    def paint(self, painter: QPainter, option, index: QModelIndex):
        btn_opt = QStyleOptionButton()
        btn_opt.rect = option.rect.adjusted(4, 4, -4, -4)
        btn_opt.text = self._label
        btn_opt.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Raised
        QApplication.style().drawControl(QStyle.ControlElement.CE_PushButton, btn_opt, painter)

    def editorEvent(self, event, model, option, index: QModelIndex):
        # Delegat tylko rysuje; kliknięcia obsługuje eventFilter na viewport()
        return False


class NotesDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option, index: QModelIndex):
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        painter.save()
        painter.setClipRect(option.rect)
        painter.drawText(
            option.rect.adjusted(4, 2, -4, -2),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            text,
        )
        painter.restore()

    def editorEvent(self, event, model, option, index: QModelIndex):
        return False


class ScrollableNotesDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        from app.monthly_view.monthly_view_ui import HoverScrollTextEdit

        editor = HoverScrollTextEdit(parent)
        editor.setReadOnly(True)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        return editor

    def setEditorData(self, editor, index):
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        editor.setPlainText(text)

    def paint(self, painter: QPainter, option, index: QModelIndex):
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        painter.save()
        painter.setClipRect(option.rect)
        painter.drawText(
            option.rect.adjusted(4, 2, -4, -2),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            text,
        )
        painter.restore()

    def editorEvent(self, event, model, option, index: QModelIndex):
        return False
