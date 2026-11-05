"""Retain an invoice table's view by record ID when its rows are refreshed."""
from contextlib import contextmanager

from PyQt6.QtCore import QItemSelectionModel


@contextmanager
def preserve_table_view(table):
    def key(row):
        item = table.item(row, 0) if row >= 0 else None
        return item.text() if item is not None else None
    selected = {key(index.row()) for index in table.selectionModel().selectedRows()}
    selected.discard(None)
    current, column = key(table.currentRow()), table.currentColumn()
    vertical, horizontal = table.verticalScrollBar().value(), table.horizontalScrollBar().value()
    top = table.rowAt(0)
    anchor = key(top)
    offset = table.rowViewportPosition(top) if top >= 0 else 0
    blocked = table.blockSignals(True)
    updating = table.updatesEnabled()
    table.setUpdatesEnabled(False)
    try:
        yield
    finally:
        try:
            rows = {key(row): row for row in range(table.rowCount())}
            table.clearSelection()
            if current is not None and current in rows:
                table.setCurrentCell(rows[current], max(0, column), QItemSelectionModel.SelectionFlag.NoUpdate)
            else:
                table.setCurrentItem(None)
            for item_id in selected:
                if item_id in rows:
                    index = table.model().index(rows[item_id], 0)
                    table.selectionModel().select(index, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
            if anchor is not None and anchor in rows:
                table.scrollToItem(table.item(rows[anchor], 0), table.ScrollHint.PositionAtTop)
                if table.verticalScrollMode() == table.ScrollMode.ScrollPerPixel:
                    table.verticalScrollBar().setValue(table.verticalScrollBar().value() - offset)
            else:
                table.verticalScrollBar().setValue(vertical)
            table.horizontalScrollBar().setValue(horizontal)
        finally:
            table.blockSignals(blocked)
            table.setUpdatesEnabled(updating)
