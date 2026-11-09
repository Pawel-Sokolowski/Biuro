"""Invoice-specific delegates on the shared virtual read-only table."""
from PyQt6.QtWidgets import QStyledItemDelegate, QStyleOptionButton, QStyle
from app.utils.item_table import ItemTable as InvoiceListTable, ItemTableModel as InvoiceListModel


class PreviewDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        button = QStyleOptionButton()
        button.rect = option.rect.adjusted(2, 1, -2, -1)
        button.text = 'Podgląd'
        button.state = QStyle.StateFlag.State_Enabled
        option.widget.style().drawControl(QStyle.ControlElement.CE_PushButton, button, painter, option.widget)
