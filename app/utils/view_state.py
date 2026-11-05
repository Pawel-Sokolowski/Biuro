"""Restore item-view state by record identity after a background update."""
from contextlib import contextmanager

from PyQt6.QtCore import QModelIndex, QPoint, QItemSelectionModel, Qt
from PyQt6.QtWidgets import QAbstractItemView, QListWidgetItem, QTreeView


class ItemViewState:
    def __init__(self, view, key):
        self.view, self.key = view, key
        self.current = self._key(view.currentIndex())
        self.column = max(0, view.currentIndex().column())
        self.selected = [key for i in view.selectionModel().selectedRows()
                         if (key := self._key(i)) is not None]
        self.expanded = {self._key(i): view.isExpanded(i) for i in self._indexes()
                         if hasattr(view, "isExpanded") and view.model().rowCount(i)}
        self.vertical = view.verticalScrollBar().value()
        self.horizontal = view.horizontalScrollBar().value()
        top = view.indexAt(QPoint(4, 1))
        self.anchor = self._key(top)
        self.offset = view.visualRect(top).top() if top.isValid() else 0

    def _key(self, index):
        return self.key(index.siblingAtColumn(0)) if index.isValid() else None

    def _indexes(self, parent=QModelIndex()):
        model = self.view.model()
        for row in range(model.rowCount(parent)):
            index = model.index(row, 0, parent)
            yield index
            if isinstance(self.view, QTreeView):
                yield from self._indexes(index)

    def restore(self):
        view = self.view
        indexes = {self._key(i): i for i in self._indexes()}
        blocked = view.blockSignals(True)
        try:
            for key, expanded in self.expanded.items():
                if key is not None and key in indexes:
                    view.setExpanded(indexes[key], expanded)
            view.clearSelection()
            current = indexes.get(self.current) if self.current is not None else None
            view.selectionModel().setCurrentIndex(
                current.siblingAtColumn(self.column) if current is not None else QModelIndex(),
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )
            for key in self.selected:
                if key in indexes:
                    view.selectionModel().select(indexes[key], QItemSelectionModel.SelectionFlag.Select
                                                 | QItemSelectionModel.SelectionFlag.Rows)
            anchor = indexes.get(self.anchor) if self.anchor is not None else None
            if anchor is not None:
                view.scrollTo(anchor, QAbstractItemView.ScrollHint.PositionAtTop)
                if view.verticalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel:
                    view.verticalScrollBar().setValue(view.verticalScrollBar().value() - self.offset)
            else:
                view.verticalScrollBar().setValue(self.vertical)
            view.horizontalScrollBar().setValue(self.horizontal)
        finally:
            view.blockSignals(blocked)


@contextmanager
def preserve_view_state(view, key):
    state = ItemViewState(view, key)
    blocked, updating = view.blockSignals(True), view.updatesEnabled()
    view.setUpdatesEnabled(False)
    try:
        yield
    finally:
        try:
            state.restore()
        finally:
            view.blockSignals(blocked)
            view.setUpdatesEnabled(updating)


def reconcile_list(view, records, key, render):
    """Reuse list items, restoring selection and the top record after changes."""
    desired = [key(record) for record in records]
    if None in desired or len(set(desired)) != len(desired):
        raise ValueError("List records require unique identities")
    role = Qt.ItemDataRole.UserRole
    with preserve_view_state(view, lambda index: index.data(role)):
        existing = {view.item(i).data(role): view.item(i) for i in range(view.count())}
        for row in range(view.count() - 1, -1, -1):
            if view.item(row).data(role) not in desired:
                view.takeItem(row)
        for position, (record_id, record) in enumerate(zip(desired, records)):
            item = existing.get(record_id)
            if item is None:
                item = QListWidgetItem()
                item.setData(role, record_id)
                view.insertItem(position, item)
            elif view.row(item) != position:
                view.takeItem(view.row(item))
                view.insertItem(position, item)
            render(item, record)
