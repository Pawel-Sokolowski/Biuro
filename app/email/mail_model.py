"""Incremental model/view mail tree keyed by database row identity."""
import json

from PyQt6.QtCore import QAbstractItemModel, QModelIndex, QItemSelectionModel, QMimeData, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QTreeView

DISPLAY = Qt.ItemDataRole.DisplayRole


class MailItem:
    """Small row descriptor; presentation roles are stored only where needed."""
    def __init__(self, texts=()):
        self._cells = [{DISPLAY: value} for value in texts]
        self._children = []
        self._parent = None
        self._view = None
        self._row = 0
        self._expanded = False
        self._is_root = False

    def columnCount(self):
        return len(self._cells)

    def data(self, column, role):
        return self._cells[column].get(role) if column < len(self._cells) else None

    def setData(self, column, role, value):
        if self.data(column, role) == value:
            return
        self._cells[column][role] = value
        if self._view is not None:
            index = self._view.model().item_index(self, column)
            self._view.model().dataChanged.emit(index, index, [int(role)])

    def text(self, column):
        return self.data(column, DISPLAY) or ""

    def setText(self, column, value):
        self.setData(column, DISPLAY, value)

    def toolTip(self, column):
        return self.data(column, Qt.ItemDataRole.ToolTipRole) or ""

    def setToolTip(self, column, value):
        self.setData(column, Qt.ItemDataRole.ToolTipRole, value)

    def font(self, column):
        return QFont(self.data(column, Qt.ItemDataRole.FontRole) or QFont())

    def setFont(self, column, value):
        self.setData(column, Qt.ItemDataRole.FontRole, value)

    def setBackground(self, column, value):
        self.setData(column, Qt.ItemDataRole.BackgroundRole, value)

    def setForeground(self, column, value):
        self.setData(column, Qt.ItemDataRole.ForegroundRole, value)

    def parent(self):
        return self._parent if self._parent is not None and not self._parent._is_root else None

    def child(self, row):
        return self._children[row]

    def childCount(self):
        return len(self._children)

    def addChild(self, child):
        child._parent = self
        child._row = len(self._children)
        self._children.append(child)

    def setExpanded(self, value):
        self._expanded = value
        if self._view is not None:
            self._view.setExpanded(self._view.model().item_index(self), value)

    def setSelected(self, value):
        if self._view is not None:
            flag = QItemSelectionModel.SelectionFlag.Select if value else QItemSelectionModel.SelectionFlag.Deselect
            self._view.selectionModel().select(self._view.model().item_index(self), flag | QItemSelectionModel.SelectionFlag.Rows)

    def isSelected(self):
        return self._view is not None and self._view.selectionModel().isSelected(self._view.model().item_index(self))


class MailModel(QAbstractItemModel):
    def __init__(self, view, identity_role):
        super().__init__(view)
        self.view, self.identity_role = view, identity_role
        self.root = MailItem()
        self.root._is_root = True
        self.headers = []
        self.items = {}

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid() and parent.column() != 0:
            return 0
        return len(self.node(parent)._children)

    def node(self, index):
        return index.internalPointer() if index.isValid() else self.root

    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        return self.createIndex(row, column, self.node(parent)._children[row])

    def parent(self, index):
        return self.item_index(self.node(index)._parent) if index.isValid() else QModelIndex()

    def item_index(self, item, column=0):
        return self.createIndex(item._row, column, item) if item is not None and item is not self.root else QModelIndex()

    def data(self, index, role=DISPLAY):
        return self.node(index).data(index.column(), role) if index.isValid() else None

    def headerData(self, section, orientation, role=DISPLAY):
        if orientation == Qt.Orientation.Horizontal and role == DISPLAY and 0 <= section < len(self.headers):
            return self.headers[section]
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled

    def mimeTypes(self):
        return ["application/x-biuro-mail-rows"]

    def mimeData(self, indexes):
        data = QMimeData()
        rows = list(dict.fromkeys(self.data(i, self.identity_role) for i in indexes if i.column() == 0))
        data.setData(self.mimeTypes()[0], json.dumps(rows).encode())
        return data

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction

    def key(self, item):
        return item.data(0, self.identity_role)

    def apply(self, roots):
        desired, parents, children = {}, {}, {None: roots}
        def collect(items, parent_key):
            for item in items:
                key = self.key(item)
                if key is None or key in desired:
                    raise ValueError("Mail rows require unique database row IDs.")
                desired[key], parents[key] = item, parent_key
                children[key] = list(item._children)
                collect(item._children, key)
        collect(roots, None)
        old = dict(self.items)

        def renumber(parent, start=0):
            for row in range(start, len(parent._children)):
                parent._children[row]._row = row

        def prune(parent):
            for row in range(len(parent._children)-1, -1, -1):
                item = parent._children[row]
                prune(item)
                parent_key = None if parent is self.root else self.key(parent)
                if self.key(item) not in desired or parents[self.key(item)] != parent_key:
                    self.beginRemoveRows(self.item_index(parent), row, row)
                    parent._children.pop(row)
                    item._view, item._parent = None, None
                    renumber(parent, row)
                    self.endRemoveRows()
        prune(self.root)

        def reconcile(parent, parent_key):
            for position, snapshot in enumerate(children[parent_key]):
                key = self.key(snapshot)
                item = old.get(key)
                if item is None:
                    item = snapshot
                    item._children = []
                if item._parent is parent and item._view is self.view:
                    current = item._row
                    if current != position:
                        self.beginMoveRows(self.item_index(parent), current, current, self.item_index(parent), position)
                        parent._children.insert(position, parent._children.pop(current))
                        renumber(parent, position)
                        self.endMoveRows()
                else:
                    self.beginInsertRows(self.item_index(parent), position, position)
                    parent._children.insert(position, item)
                    item._parent, item._view = parent, self.view
                    renumber(parent, position)
                    self.endInsertRows()
                if item._cells != snapshot._cells:
                    item._cells = snapshot._cells
                    self.dataChanged.emit(self.item_index(item), self.item_index(item, self.columnCount()-1), [])
                item._expanded = item._expanded or snapshot._expanded
                self.items[key] = item
                reconcile(item, key)
                if item._expanded:
                    self.view.setExpanded(self.item_index(item), True)
        reconcile(self.root, None)
        self.items = {key: self.items[key] for key in desired}


class MailTreeView(QTreeView):
    """Qt view with row-oriented helpers shared by existing mail actions."""
    itemSelectionChanged = pyqtSignal()

    def __init__(self, identity_role, parent=None):
        super().__init__(parent)
        self.setModel(MailModel(self, identity_role))
        self.selectionModel().selectionChanged.connect(lambda *_: self.itemSelectionChanged.emit())
        self.expanded.connect(lambda index: setattr(index.internalPointer(), "_expanded", True))
        self.collapsed.connect(lambda index: setattr(index.internalPointer(), "_expanded", False))

    def setColumnCount(self, count):
        self.model().beginResetModel()
        self.model().headers = [""] * count
        self.model().endResetModel()

    def columnCount(self):
        return self.model().columnCount()

    def setHeaderLabels(self, labels):
        self.model().headers = list(labels)
        self.model().headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(labels)-1)

    def clear(self):
        self.model().apply([])

    def apply_items(self, roots):
        self.model().apply(roots)

    def resolve(self, item):
        return self.model().items.get(self.model().key(item)) if item is not None else None

    def all_items(self):
        return iter(self.model().items.values())

    def selectedItems(self):
        return [index.internalPointer() for index in self.selectionModel().selectedRows()]

    def currentItem(self):
        return self.currentIndex().internalPointer() if self.currentIndex().isValid() else None

    def setCurrentItem(self, item, column=0, flags=QItemSelectionModel.SelectionFlag.ClearAndSelect):
        item = self.resolve(item)
        self.selectionModel().setCurrentIndex(self.model().item_index(item, column), flags | QItemSelectionModel.SelectionFlag.Rows)

    def topLevelItem(self, row):
        return self.model().root._children[row]

    def topLevelItemCount(self):
        return self.model().rowCount()

    def indexOfTopLevelItem(self, item):
        return item._row if item._parent is self.model().root else -1

    def itemAt(self, position):
        index = self.indexAt(position)
        return index.internalPointer() if index.isValid() else None

    def scrollToItem(self, item):
        self.scrollTo(self.model().item_index(item))
