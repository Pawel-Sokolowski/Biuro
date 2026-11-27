"""QTableWidget with the first column pinned during horizontal scrolling."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QTableView,
    QTableWidget,
)

from app.utils.table_sorting import (
    apply_header_sort,
    enable_header_sort,
)


class FrozenFirstColumnTable(QTableWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.frozen_column = QTableView(self)
        frozen = self.frozen_column

        frozen.setModel(self.model())
        frozen.setSelectionModel(self.selectionModel())

        frozen.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        frozen.setFrameShape(QFrame.Shape.NoFrame)

        frozen.verticalHeader().hide()

        frozen.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        frozen.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        frozen.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        frozen.setSelectionBehavior(self.selectionBehavior())
        frozen.setSelectionMode(self.selectionMode())

        frozen.clicked.connect(
            lambda index: self._forward_cell(index, False)
        )
        frozen.doubleClicked.connect(
            lambda index: self._forward_cell(index, True)
        )

        self.horizontalHeader().sectionResized.connect(
            self._column_resized
        )
        frozen.horizontalHeader().sectionResized.connect(
            self._frozen_resized
        )

        self.verticalHeader().sectionResized.connect(
            self._row_resized
        )

        self.horizontalHeader().geometriesChanged.connect(
            self._position_frozen
        )
        self.verticalHeader().geometriesChanged.connect(
            self._position_frozen
        )

        self.verticalScrollBar().valueChanged.connect(
            frozen.verticalScrollBar().setValue
        )
        frozen.verticalScrollBar().valueChanged.connect(
            self.verticalScrollBar().setValue
        )

        self.model().columnsInserted.connect(self._sync_columns)
        self.model().columnsRemoved.connect(self._sync_columns)
        self.model().modelReset.connect(self._model_reset)
        self.model().rowsInserted.connect(self._sync_rows)
        self.model().rowsRemoved.connect(self._sync_rows)
        self.model().layoutChanged.connect(self._layout_changed)

        # Let the sorting helper know that this table has an additional
        # view attached to the same model.
        self._sort_companion_views = (frozen,)

        # Sorting is installed only once on the real QTableWidget.
        # The frozen header is passed only for visual indicator
        # synchronization. It does not get its own sorting handler.
        enable_header_sort(
            self,
            indicator_headers=(frozen.horizontalHeader(),),
        )

        # A double-click on the frozen header forwards to the same guarded
        # sorting operation used by the main header.
        frozen.horizontalHeader().setSectionsClickable(True)
        frozen.horizontalHeader().sectionDoubleClicked.connect(
            self._sort_from_frozen_header
        )

        self._sync_columns()
        self._sync_rows()

    def _sort_from_frozen_header(self, column):
        if column < 0 or column >= self.columnCount():
            return

        apply_header_sort(
            self,
            column=column,
            toggle=True,
        )

    def _forward_cell(self, index, double):
        if not index.isValid():
            return

        self.setFocus()

        row = index.row()
        item = self.item(row, 0)

        if double:
            self.cellDoubleClicked.emit(row, 0)

            if item is not None:
                self.itemDoubleClicked.emit(item)
        else:
            self.cellClicked.emit(row, 0)

            if item is not None:
                self.itemClicked.emit(item)

    def _row_resized(self, row, old_size, new_size):
        del old_size

        if not hasattr(self, "frozen_column"):
            return

        if 0 <= row < self.rowCount():
            self.frozen_column.setRowHeight(row, new_size)

    def _model_reset(self, *args):
        del args

        self._sync_columns()
        self._sync_rows()

    def _layout_changed(self, *args):
        del args

        self._sync_rows()
        self._position_frozen()

    def _sync_columns(self, *args):
        del args

        if not hasattr(self, "frozen_column"):
            return

        frozen = self.frozen_column

        for column in range(self.columnCount()):
            frozen.setColumnHidden(column, column != 0)

        if self.columnCount() > 0:
            frozen.setColumnWidth(0, self.columnWidth(0))

        self._position_frozen()

    def _sync_rows(self, *args):
        del args

        if not hasattr(self, "frozen_column"):
            return

        frozen = self.frozen_column

        for row in range(self.rowCount()):
            frozen.setRowHeight(row, self.rowHeight(row))

        frozen.verticalScrollBar().setValue(
            self.verticalScrollBar().value()
        )

        self._position_frozen()

    def _column_resized(self, column, old_size, new_size):
        del old_size

        if not hasattr(self, "frozen_column"):
            return

        if column == 0:
            frozen_width = self.frozen_column.columnWidth(0)

            if frozen_width != new_size:
                self.frozen_column.setColumnWidth(0, new_size)

        self._position_frozen()

    def _frozen_resized(self, column, old_size, new_size):
        del old_size

        if column != 0:
            return

        if new_size != self.columnWidth(0):
            self.setColumnWidth(0, new_size)

    def _position_frozen(self):
        if not hasattr(self, "frozen_column"):
            return

        frozen = self.frozen_column
        has_columns = self.columnCount() > 0

        frozen.setVisible(has_columns)

        if not has_columns:
            return

        main_header = self.horizontalHeader()
        frozen_header = frozen.horizontalHeader()

        header_visible = not main_header.isHidden()
        header_height = main_header.height() if header_visible else 0

        frozen_header.setVisible(header_visible)

        if header_visible:
            frozen_header.setFixedHeight(header_height)

        if self.verticalHeader().isHidden():
            left_offset = 0
        else:
            left_offset = self.verticalHeader().width()

        frozen.setGeometry(
            self.frameWidth() + left_offset,
            self.frameWidth(),
            self.columnWidth(0),
            self.viewport().height() + header_height,
        )

        frozen.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_frozen()

    def showEvent(self, event):
        super().showEvent(event)

        frozen = self.frozen_column

        frozen.setAlternatingRowColors(
            self.alternatingRowColors()
        )
        frozen.setSelectionBehavior(
            self.selectionBehavior()
        )
        frozen.setSelectionMode(
            self.selectionMode()
        )
        frozen.setVerticalScrollMode(
            self.verticalScrollMode()
        )

        self._sync_columns()
        self._sync_rows()

    def scrollTo(
        self,
        index,
        hint=QAbstractItemView.ScrollHint.EnsureVisible,
    ):
        if (
            hasattr(self, "frozen_column")
            and index.isValid()
            and index.column() == 0
        ):
            self.frozen_column.scrollTo(index, hint)
            return

        super().scrollTo(index, hint)