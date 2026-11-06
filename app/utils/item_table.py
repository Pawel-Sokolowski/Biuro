"""Virtual read-only table with the small item API used by invoice actions."""
from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtWidgets import QTableView, QTableWidgetItem


class _Cell:
    def __init__(self, item, row, column):
        self.item, self._row, self._column = item, row, column

    def row(self):
        return self._row

    def column(self):
        return self._column

    def __getattr__(self, name):
        return getattr(self.item, name)


class ItemTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows, self.headers = [], []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid():
            cell = self.rows[index.row()][index.column()]
            return cell.data(role) if cell else None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section] if orientation == Qt.Orientation.Horizontal else section + 1

    def flags(self, index):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable if index.isValid() else Qt.ItemFlag.NoItemFlags

    def replace(self, rows):
        for row_number, row in enumerate(rows):
            for column, cell in enumerate(row):
                if cell is not None:
                    cell._row, cell._column = row_number, column
        def ids(values):
            return [row[0].text() if row and row[0] else None for row in values]
        previous = self.rows
        if ids(previous) != ids(rows):
            self.beginResetModel()
            self.rows = rows
            self.endResetModel()
            return
        self.rows = rows
        roles = (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole,
                 Qt.ItemDataRole.ForegroundRole, Qt.ItemDataRole.FontRole)
        def signature(cell):
            return tuple(cell.data(role) for role in roles) if cell else None
        for row, (old, new) in enumerate(zip(previous, rows)):
            if any(signature(cell) != signature(other) for cell, other in zip(old, new)):
                self.dataChanged.emit(self.index(row, 0), self.index(row, len(self.headers) - 1), list(roles))


class ItemTable(QTableView):
    cellClicked = pyqtSignal(int, int)
    cellDoubleClicked = pyqtSignal(int, int)

    def __init__(self, headers, parent=None):
        super().__init__(parent)
        self._records = ItemTableModel(self)
        self._records.headers = list(headers)
        self.setModel(self._records)
        self._pending = None
        self.clicked.connect(lambda index: self.cellClicked.emit(index.row(), index.column()))
        self.doubleClicked.connect(lambda index: self.cellDoubleClicked.emit(index.row(), index.column()))

    def begin_batch(self):
        self._pending = [list(row) for row in self._records.rows]

    def currentChanged(self, current, previous):
        if getattr(self, '_restoring_view', False):
            self.viewport().update()
            return
        super().currentChanged(current, previous)

    def end_batch(self):
        pending, self._pending = self._pending, None
        if pending is not None:
            self._records.replace(pending)

    def rowCount(self):
        return len(self._pending if self._pending is not None else self._records.rows)

    def columnCount(self):
        return len(self._records.headers)

    def setRowCount(self, count):
        rows = self._pending if self._pending is not None else [list(row) for row in self._records.rows]
        del rows[count:]
        rows.extend([[None] * self.columnCount() for _ in range(count - len(rows))])
        if self._pending is None:
            self._records.replace(rows)

    def setItem(self, row, column, item):
        rows = self._pending if self._pending is not None else self._records.rows
        rows[row][column] = _Cell(item, row, column)
        if self._pending is None:
            index = self._records.index(row, column)
            self._records.dataChanged.emit(index, index)

    def insertRow(self, row):
        rows = self._pending if self._pending is not None else [list(row) for row in self._records.rows]
        rows.insert(row, [None] * self.columnCount())
        if self._pending is None:
            self._records.replace(rows)

    def setHorizontalHeaderLabels(self, headers):
        self._records.headers = list(headers)
        self._records.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(headers) - 1)

    def item(self, row, column):
        rows = self._pending if self._pending is not None else self._records.rows
        return rows[row][column] if 0 <= row < len(rows) and 0 <= column < self.columnCount() else None

    def selectedItems(self):
        return [self.item(index.row(), index.column()) for index in self.selectedIndexes() if self.item(index.row(), index.column())]

    def currentRow(self):
        return self.currentIndex().row()

    def currentColumn(self):
        return self.currentIndex().column()

    def setCurrentCell(self, row, column, command=None):
        index = self._records.index(row, column)
        if command is None:
            self.setCurrentIndex(index)
        else:
            self.selectionModel().setCurrentIndex(index, command)

    def setCurrentItem(self, item):
        self.setCurrentIndex(QModelIndex() if item is None else self._records.index(item.row(), item.column()))

    def scrollToItem(self, item, hint=QTableView.ScrollHint.EnsureVisible):
        self.scrollTo(self._records.index(item.row(), item.column()), hint)

    def horizontalHeaderItem(self, column):
        return QTableWidgetItem(self._records.headers[column])

    def cellWidget(self, row, column):
        return None


