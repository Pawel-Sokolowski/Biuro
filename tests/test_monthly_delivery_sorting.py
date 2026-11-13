from unittest import TestCase
from PyQt6.QtCore import QPersistentModelIndex, Qt
from PyQt6.QtWidgets import QTableView
from app.monthly_view.monthly_view_model import MonthlyViewModel
from app.monthly_view.monthly_view_ui import MonthlyViewPage
from tests.base import QT_APP


def rows():
    return [
        {'id': 1, 'nazwa': 'A', 'dokumenty_dostarczone': 1, 'data_dostarczenia': '2026-01-31'},
        {'id': 2, 'nazwa': 'B', 'dokumenty_dostarczone': 1, 'data_dostarczenia': '2026-02-01'},
        {'id': 3, 'nazwa': 'C', 'dokumenty_dostarczone': 1, 'data_dostarczenia': '2025-12-15'},
        {'id': 4, 'nazwa': 'D', 'dokumenty_dostarczone': 0, 'data_dostarczenia': '2024-01-01'},
        {'id': 5, 'nazwa': 'E', 'dokumenty_dostarczone': 1, 'data_dostarczenia': None},
    ]


def ids(model):
    return [model.get_row(index)['id'] for index in range(model.rowCount())]


class MonthlyDeliverySortingTests(TestCase):
    def test_header_toggles_chronological_order_in_both_departments_and_keeps_undated_last(self):
        self.assertIsNotNone(QT_APP)
        for mode, column in (('ksiegowosc', 3), ('kadry', 1)):
            with self.subTest(mode=mode):
                table = QTableView()
                model = MonthlyViewModel(rows(), mode)
                table.setModel(model)
                try:
                    MonthlyViewPage._sort_delivery(table, column)
                    self.assertEqual(ids(model), [3, 1, 2, 4, 5])
                    MonthlyViewPage._sort_delivery(table, column)
                    self.assertEqual(ids(model), [2, 1, 3, 4, 5])
                    self.assertEqual(table.horizontalHeader().sortIndicatorSection(), column)
                    self.assertEqual(table.horizontalHeader().sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)
                finally:
                    table.close()

    def test_sorting_keeps_persistent_selection_on_the_same_company(self):
        model = MonthlyViewModel(rows(), 'ksiegowosc')
        selected = QPersistentModelIndex(model.index(0, 0))
        model.sort(3, Qt.SortOrder.AscendingOrder)
        self.assertEqual(selected.data(Qt.ItemDataRole.UserRole)['id'], 1)
        self.assertEqual(selected.row(), 1)
        model.sort(3, Qt.SortOrder.DescendingOrder)
        self.assertEqual(selected.data(Qt.ItemDataRole.UserRole)['id'], 1)

    def test_sort_survives_refresh_and_delivery_date_edits(self):
        model = MonthlyViewModel(rows(), 'kadry')
        model.sort(1, Qt.SortOrder.AscendingOrder)
        refreshed = rows()
        refreshed[1]['data_dostarczenia'] = '2025-11-01'
        model.set_rows(refreshed)
        self.assertEqual(ids(model), [2, 3, 1, 4, 5])
        model.setData(model.index(3, 1), '2025-01-01', model.DATE_ROLE)
        self.assertEqual(ids(model), [4, 2, 3, 1, 5])
        self.assertEqual(model.index(0, 1).data(), '01.01.2025')
