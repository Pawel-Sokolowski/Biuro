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
    if hasattr(table, 'begin_batch'):
        table.begin_batch()
    try:
        yield
    finally:
        try:
            if hasattr(table, 'end_batch'):
                table.end_batch()
                table.doItemsLayout()
                table._restoring_view = True
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
                    delta = table.rowViewportPosition(rows[anchor]) - offset
                    table.verticalScrollBar().setValue(table.verticalScrollBar().value() + delta)
                else:
                    # Qt may defer scrollTo() while updates are disabled. Restore
                    # the visible section offset explicitly before re-enabling.
                    header = table.verticalHeader()
                    visual = header.visualIndex(rows[anchor])
                    visible = sum(not header.isSectionHidden(header.logicalIndex(i)) for i in range(visual))
                    table.verticalScrollBar().setValue(visible)
            else:
                table.verticalScrollBar().setValue(vertical)
            table.horizontalScrollBar().setValue(horizontal)
        finally:
            if hasattr(table, '_restoring_view'):
                table._restoring_view = False
            table.blockSignals(blocked)
            table.setUpdatesEnabled(updating)
