"""Company identity remains visible while scrolling wide checking reports."""
import unittest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest,QSignalSpy
from PyQt6.QtWidgets import QApplication,QTableWidgetItem,QAbstractItemView
from app.utils.frozen_table import FrozenFirstColumnTable

_app = QApplication.instance() or QApplication(['frozen-company'])


class FrozenCompanyColumnTests(unittest.TestCase):
    def test_scrolling_resizing_and_selection_keep_the_same_company_row(self):
        table = FrozenFirstColumnTable(40,8)
        self.addCleanup(table.close)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setHorizontalHeaderLabels(['Firma',*['Dokument '+str(n) for n in range(7)]])
        for row in range(40):
            table.setItem(row,0,QTableWidgetItem('Firma '+str(row)))
        for column in range(8):
            table.setColumnWidth(column,160)
        table.resize(620,320)
        table.show()
        _app.processEvents()
        frozen = table.frozen_column
        self.assertIs(frozen.model(),table.model())
        self.assertIs(frozen.selectionModel(),table.selectionModel())
        table.horizontalScrollBar().setValue(table.horizontalScrollBar().maximum())
        table.verticalScrollBar().setValue(10)
        _app.processEvents()
        self.assertTrue(frozen.isVisible())
        self.assertEqual(frozen.visualRect(table.model().index(10,0)).left(),0)
        self.assertEqual(frozen.verticalScrollBar().value(),table.verticalScrollBar().value())
        table.setColumnWidth(0,210)
        self.assertEqual(frozen.width(),210)
        table.selectRow(10)
        self.assertEqual([index.row() for index in frozen.selectionModel().selectedRows()],[10])
        index = table.model().index(10,0)
        spy = QSignalSpy(table.cellDoubleClicked)
        QTest.mouseClick(frozen.viewport(),Qt.MouseButton.LeftButton,pos=frozen.visualRect(index).center())
        QTest.mouseDClick(frozen.viewport(),Qt.MouseButton.LeftButton,pos=frozen.visualRect(index).center())
        self.assertTrue(any(list(event)==[10,0] for event in spy))
        table.setColumnCount(10)
        self.assertTrue(all(frozen.isColumnHidden(column) for column in range(1,10)))
