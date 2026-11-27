"""Order Qt table columns from their headers without moving rows while filling them."""
from datetime import date
from decimal import Decimal
import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem

from app.utils.polish_sort import polish_sort_key

ASCENDING = Qt.SortOrder.AscendingOrder
DESCENDING = Qt.SortOrder.DescendingOrder


def sort_value(value):
    text = str(value if value is not None else '').strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[ T].*)?', text):
        try:
            return 0, date.fromisoformat(text[:10]).toordinal(), text[10:]
        except ValueError:
            pass
    local_date = re.fullmatch(r'(\d{1,2})[./](\d{1,2})[./](\d{4})([ T].*)?', text)
    if local_date:
        day, month, year = map(int, local_date.groups()[:3])
        try:
            return 0, date(year, month, day).toordinal(), local_date[4] or ''
        except ValueError:
            pass
    if re.fullmatch(r'\d+:\d{2}(?::\d{2})?', text):
        parts = [int(part) for part in text.split(':')]
        return 1, parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) == 3 else 0)
    number = text.removesuffix('PLN').removesuffix('zł').removesuffix('%')
    number = number.replace(' ', '').replace('\u00a0', '').replace(',', '.')
    if re.fullmatch(r'[+-]?\d+(?:\.\d+)?', number):
        return 1, Decimal(number)
    return 2, polish_sort_key(text)


class SortableItem(QTableWidgetItem):
    def __lt__(self, other):
        if self.data(Qt.ItemDataRole.CheckStateRole) is not None and other.data(Qt.ItemDataRole.CheckStateRole) is not None:
            return self.checkState().value < other.checkState().value
        return sort_value(self.data(Qt.ItemDataRole.DisplayRole)) < sort_value(other.data(Qt.ItemDataRole.DisplayRole))


def enable_header_sort(view, *additional_headers):
    headers = (view.horizontalHeader(), *additional_headers)

    def sort(column):
        previous, direction = getattr(view, '_header_sort', (-1, DESCENDING))
        order = DESCENDING if previous == column and direction == ASCENDING else ASCENDING
        view._header_sort = column, order
        apply_header_sort(view)
        for header in headers:
            header.setSortIndicator(column, order)
            header.setSortIndicatorShown(True)

    for header in headers:
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(False)
        header.sectionDoubleClicked.connect(sort)


def apply_header_sort(view):
    selected = getattr(view, '_header_sort', None)
    if selected is not None and 0 <= selected[0] < view.model().columnCount():
        if isinstance(view, QTableWidget):
            view.sortItems(*selected)
        else:
            view.model().sort(*selected)
