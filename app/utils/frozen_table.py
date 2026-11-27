"""Item table with a shared-model first column pinned during horizontal scrolling."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTableWidget,QTableView,QAbstractItemView,QFrame
from app.utils.table_sorting import enable_header_sort


class FrozenFirstColumnTable(QTableWidget):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.frozen_column = QTableView(self)
        frozen = self.frozen_column
        frozen.setModel(self.model())
        enable_header_sort(self, frozen.horizontalHeader())
        frozen.setSelectionModel(self.selectionModel())
        frozen.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        frozen.setFrameShape(QFrame.Shape.NoFrame)
        frozen.verticalHeader().hide()
        frozen.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        frozen.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        frozen.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        frozen.setSelectionBehavior(self.selectionBehavior())
        frozen.setSelectionMode(self.selectionMode())
        frozen.clicked.connect(lambda index:self._forward_cell(index,False))
        frozen.doubleClicked.connect(lambda index:self._forward_cell(index,True))
        self.horizontalHeader().sectionResized.connect(self._column_resized)
        frozen.horizontalHeader().sectionResized.connect(self._frozen_resized)
        self.verticalHeader().sectionResized.connect(lambda row,old,size:frozen.setRowHeight(row,size))
        self.horizontalHeader().geometriesChanged.connect(self._position_frozen)
        self.verticalHeader().geometriesChanged.connect(self._position_frozen)
        self.verticalScrollBar().valueChanged.connect(frozen.verticalScrollBar().setValue)
        frozen.verticalScrollBar().valueChanged.connect(self.verticalScrollBar().setValue)
        self.model().columnsInserted.connect(self._sync_columns)
        self.model().columnsRemoved.connect(self._sync_columns)
        self.model().modelReset.connect(self._sync_columns)
        self.model().rowsInserted.connect(self._sync_rows)
        self._sync_columns()
        self._sync_rows()

    def _forward_cell(self,index,double):
        self.setFocus()
        item = self.item(index.row(),0)
        if double:
            self.cellDoubleClicked.emit(index.row(),0)
            if item is not None:
                self.itemDoubleClicked.emit(item)
        else:
            self.cellClicked.emit(index.row(),0)
            if item is not None:
                self.itemClicked.emit(item)

    def _sync_columns(self,*_):
        for column in range(self.columnCount()):
            self.frozen_column.setColumnHidden(column,column!=0)
        if self.columnCount():
            self.frozen_column.setColumnWidth(0,self.columnWidth(0))
        self._position_frozen()

    def _sync_rows(self,*_):
        for row in range(self.rowCount()):
            self.frozen_column.setRowHeight(row,self.rowHeight(row))
        self.frozen_column.verticalScrollBar().setValue(self.verticalScrollBar().value())
        self._position_frozen()

    def _column_resized(self,column,old,size):
        if column==0:
            self.frozen_column.setColumnWidth(0,size)
        self._position_frozen()

    def _frozen_resized(self,column,old,size):
        if column==0 and size!=self.columnWidth(0):
            self.setColumnWidth(0,size)

    def _position_frozen(self):
        if not hasattr(self,'frozen_column'):
            return
        frozen = self.frozen_column
        frozen.setVisible(self.columnCount()>0)
        if not self.columnCount():
            return
        header = self.horizontalHeader().height() if not self.horizontalHeader().isHidden() else 0
        frozen.horizontalHeader().setVisible(not self.horizontalHeader().isHidden())
        frozen.horizontalHeader().setFixedHeight(header)
        left = 0 if self.verticalHeader().isHidden() else self.verticalHeader().width()
        frozen.setGeometry(self.frameWidth()+left,self.frameWidth(),self.columnWidth(0),self.viewport().height()+header)
        frozen.raise_()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self._position_frozen()

    def showEvent(self,event):
        super().showEvent(event)
        self.frozen_column.setAlternatingRowColors(self.alternatingRowColors())
        self.frozen_column.setSelectionBehavior(self.selectionBehavior())
        self.frozen_column.setSelectionMode(self.selectionMode())
        self.frozen_column.setVerticalScrollMode(self.verticalScrollMode())
        self._sync_columns()
        self._sync_rows()

    def scrollTo(self,index,hint=QAbstractItemView.ScrollHint.EnsureVisible):
        if hasattr(self,'frozen_column') and index.column()==0:
            self.frozen_column.scrollTo(index,hint)
        else:
            super().scrollTo(index,hint)
