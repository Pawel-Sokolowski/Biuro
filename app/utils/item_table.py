"""Record-backed Qt table; lightweight item adapters are created only on demand."""

from dataclasses import replace
from difflib import SequenceMatcher
from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QFont
from PyQt6.QtWidgets import QTableView, QTableWidgetItem
from app.utils.table_records import TableRecord

DISPLAY = Qt.ItemDataRole.DisplayRole


class _Cell:
    def __init__(self, table, row, column):
        self.table, self._row, self._column = table, row, column

    def row(self):
        return self._row

    def column(self):
        return self._column

    def text(self):
        return self.data(DISPLAY) or ""

    def setText(self, text):
        self.setData(DISPLAY, str(text))

    def toolTip(self):
        return self.data(Qt.ItemDataRole.ToolTipRole) or ""

    def setToolTip(self, text):
        self.setData(Qt.ItemDataRole.ToolTipRole, text)

    def font(self):
        return self.data(Qt.ItemDataRole.FontRole) or QFont()

    def setFont(self, font):
        self.setData(Qt.ItemDataRole.FontRole, font)

    def foreground(self):
        value = self.data(Qt.ItemDataRole.ForegroundRole)
        return QBrush(value) if value is not None else QBrush()

    def setForeground(self, value):
        self.setData(Qt.ItemDataRole.ForegroundRole, QBrush(value))

    def setTextAlignment(self, value):
        self.setData(Qt.ItemDataRole.TextAlignmentRole, value)

    def flags(self):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, role):
        record = self.table._rows()[self._row]
        return (
            record.values[self._column]
            if role == DISPLAY
            else record.roles.get(self._column, {}).get(role)
        )

    def setData(self, role, value):
        self.table._set_value(self._row, self._column, role, value)


class ItemTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows, self.headers = [], []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=DISPLAY):
        if index.isValid():
            record = self.rows[index.row()]
            return (
                record.values[index.column()]
                if role == DISPLAY
                else record.roles.get(index.column(), {}).get(role)
            )

    def headerData(self, section, orientation, role=DISPLAY):
        if role == DISPLAY:
            return (
                self.headers[section]
                if orientation == Qt.Orientation.Horizontal
                else section + 1
            )

    def flags(self, index):
        return (
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if index.isValid()
            else Qt.ItemFlag.NoItemFlags
        )

    def replace(self, records):
        old_keys, new_keys = [r.key for r in self.rows], [r.key for r in records]
        if old_keys != new_keys:
            if len(set(new_keys)) != len(new_keys) or len(set(old_keys)) != len(
                old_keys
            ):
                self.beginResetModel()
                self.rows = records
                self.endResetModel()
                return
            # Apply edits from the end so the remaining old offsets stay valid.
            for tag, start, end, new_start, new_end in reversed(
                SequenceMatcher(None, old_keys, new_keys, autojunk=False).get_opcodes()
            ):
                if tag == "equal":
                    continue
                if end > start:
                    self.beginRemoveRows(QModelIndex(), start, end - 1)
                    del self.rows[start:end]
                    self.endRemoveRows()
                if new_end > new_start:
                    self.beginInsertRows(
                        QModelIndex(), start, start + new_end - new_start - 1
                    )
                    self.rows[start:start] = records[new_start:new_end]
                    self.endInsertRows()
        for row, (old, new) in enumerate(zip(self.rows, records)):
            if old == new:
                continue
            changed = [
                col
                for col in range(len(self.headers))
                if old.values[col] != new.values[col]
                or old.roles.get(col) != new.roles.get(col)
            ]
            self.rows[row] = new
            if changed:
                self.dataChanged.emit(
                    self.index(row, min(changed)), self.index(row, max(changed))
                )


class ItemTable(QTableView):
    cellClicked = pyqtSignal(int, int)
    cellDoubleClicked = pyqtSignal(int, int)

    def __init__(self, headers, parent=None):
        super().__init__(parent)
        self._records = ItemTableModel(self)
        self._records.headers = list(headers)
        self.setModel(self._records)
        self._pending = None
        self.clicked.connect(
            lambda index: self.cellClicked.emit(index.row(), index.column())
        )
        self.doubleClicked.connect(
            lambda index: self.cellDoubleClicked.emit(index.row(), index.column())
        )

    def _rows(self):
        return self._pending if self._pending is not None else self._records.rows

    def begin_batch(self):
        self._pending = list(self._records.rows)

    def end_batch(self):
        pending, self._pending = self._pending, None
        if pending is not None:
            self._records.replace(pending)

    def set_records(self, records):
        if self._pending is not None:
            self._pending = list(records)
        else:
            self._records.replace(list(records))

    def currentChanged(self, current, previous):
        if getattr(self, "_restoring_view", False):
            self.viewport().update()
        else:
            super().currentChanged(current, previous)

    def rowCount(self):
        return len(self._rows())

    def columnCount(self):
        return len(self._records.headers)

    def setRowCount(self, count):
        rows = list(self._rows())
        del rows[count:]
        rows.extend(
            TableRecord(object(), ("",) * self.columnCount())
            for _ in range(count - len(rows))
        )
        self.set_records(rows)

    def _set_value(self, row, column, role, value):
        rows = self._rows()
        record = rows[row]
        if role == DISPLAY:
            values = list(record.values)
            values[column] = str(value)
            rows[row] = replace(
                record,
                values=tuple(values),
                key=str(value) if column == 0 else record.key,
            )
        else:
            roles = {col: dict(data) for col, data in record.roles.items()}
            roles.setdefault(column, {})[role] = value
            rows[row] = replace(record, roles=roles)
        if self._pending is None:
            index = self._records.index(row, column)
            self._records.dataChanged.emit(index, index)

    def setItem(self, row, column, item):
        self._set_value(row, column, DISPLAY, item.text())
        for role in (
            Qt.ItemDataRole.ToolTipRole,
            Qt.ItemDataRole.ForegroundRole,
            Qt.ItemDataRole.BackgroundRole,
            Qt.ItemDataRole.FontRole,
            Qt.ItemDataRole.TextAlignmentRole,
            Qt.ItemDataRole.UserRole,
        ):
            value = item.data(role)
            if value is not None:
                self._set_value(row, column, role, value)

    def insertRow(self, row):
        rows = list(self._rows())
        rows.insert(row, TableRecord(object(), ("",) * self.columnCount()))
        self.set_records(rows)

    def setHorizontalHeaderLabels(self, headers):
        self._records.headers = list(headers)
        self._records.headerDataChanged.emit(
            Qt.Orientation.Horizontal, 0, len(headers) - 1
        )

    def item(self, row, column):
        return (
            _Cell(self, row, column)
            if 0 <= row < self.rowCount() and 0 <= column < self.columnCount()
            else None
        )

    def selectedItems(self):
        return [self.item(i.row(), i.column()) for i in self.selectedIndexes()]

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
        self.setCurrentIndex(
            QModelIndex()
            if item is None
            else self._records.index(item.row(), item.column())
        )

    def scrollToItem(self, item, hint=QTableView.ScrollHint.EnsureVisible):
        self.scrollTo(self._records.index(item.row(), item.column()), hint)

    def horizontalHeaderItem(self, column):
        return QTableWidgetItem(self._records.headers[column])

    def cellWidget(self, row, column):
        return None
