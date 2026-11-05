"""Generated tree/paging transitions checked against Qt's model invariants."""
import unittest

from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule
from PyQt6.QtCore import QItemSelectionModel, QPersistentModelIndex, Qt
from PyQt6.QtTest import QAbstractItemModelTester, QSignalSpy
from PyQt6.QtWidgets import QApplication

from app.email.mail_model import MailItem, MailTreeView

_app = QApplication.instance() or QApplication([])
ROLE = int(Qt.ItemDataRole.UserRole) + 2


def item(row_id, label=None, children=()):
    node = MailItem([label or str(row_id), 'sender', '2026-11-04'])
    node.setData(0, ROLE, row_id)
    for child in children:
        node.addChild(child)
    return node


class MailModelTests(unittest.TestCase):
    def view(self):
        view = MailTreeView(ROLE)
        view.setHeaderLabels(['Subject', 'Sender', 'Date'])
        tester = QAbstractItemModelTester(view.model(), QAbstractItemModelTester.FailureReportingMode.Fatal)
        view._tester = tester
        self.addCleanup(view.deleteLater)
        return view

    def test_unchanged_refresh_preserves_nodes_indexes_and_selection_without_reset(self):
        view = self.view()
        view.apply_items([item(1, children=[item(2)]), item(3)])
        original = view.model().items[2]
        persistent = QPersistentModelIndex(view.model().item_index(original))
        original.setSelected(True)
        reset, changed = QSignalSpy(view.model().modelReset), QSignalSpy(view.model().dataChanged)
        inserted, removed = QSignalSpy(view.model().rowsInserted), QSignalSpy(view.model().rowsRemoved)
        view.apply_items([item(1, children=[item(2)]), item(3)])
        self.assertIs(view.model().items[2], original)
        self.assertTrue(persistent.isValid())
        self.assertTrue(original.isSelected())
        self.assertEqual((len(reset), len(changed), len(inserted), len(removed)), (0, 0, 0, 0))

    def test_append_changes_only_new_rows_and_preserves_expansion(self):
        view = self.view()
        view.apply_items([item(1, children=[item(2)])])
        view.topLevelItem(0).setExpanded(True)
        before = QSignalSpy(view.model().rowsInserted)
        view.apply_items([item(1, children=[item(2), item(3)]), item(4)])
        self.assertEqual(len(before), 2)
        self.assertTrue(view.isExpanded(view.model().item_index(view.model().items[1])))
        self.assertEqual(view.model().items[3].parent(), view.model().items[1])

    def test_invalid_duplicate_rows_do_not_mutate_existing_model(self):
        view = self.view()
        view.apply_items([item(1)])
        with self.assertRaises(ValueError):
            view.apply_items([item(2), item(2)])
        self.assertEqual(list(view.model().items), [1])


class MailTreeMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.view = MailTreeView(ROLE)
        self.view.setHeaderLabels(['Subject', 'Sender', 'Date'])
        self.tester = QAbstractItemModelTester(self.view.model(), QAbstractItemModelTester.FailureReportingMode.Fatal)
        self.expected = []

    @rule(rows=st.lists(st.integers(min_value=1, max_value=30), unique=True, max_size=20), threaded=st.booleans())
    def refresh_page(self, rows, threaded):
        # Reorders, insertions, deletions and regrouping cover repeated refreshes
        # during pagination without relying on a fixed happy-path sequence.
        nodes = [item(row) for row in rows]
        roots = nodes
        if threaded and nodes:
            roots = [nodes[0]]
            for child in nodes[1:]:
                nodes[0].addChild(child)
        self.view.apply_items(roots)
        self.expected = rows

    @rule(selected=st.integers(min_value=1, max_value=30))
    def select_then_read_state_change(self, selected):
        node = self.view.model().items.get(selected)
        if node is not None:
            self.view.setCurrentItem(node, 0, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            node.setSelected(True)
            node.setToolTip(0, 'read')
            assert node.isSelected()
            assert node.data(0, ROLE) == selected

    @invariant()
    def rows_and_parent_indexes_are_consistent(self):
        model = self.view.model()
        assert set(model.items) == set(self.expected)
        def walk(parent):
            for row, node in enumerate(parent._children):
                index = model.item_index(node)
                assert index.row() == row
                assert model.node(model.parent(index)) is parent
                assert model.data(index, ROLE) == node.data(0, ROLE)
                walk(node)
        walk(model.root)

    def teardown(self):
        self.view.deleteLater()


TestMailTreeMachine = MailTreeMachine.TestCase
TestMailTreeMachine.settings = settings(max_examples=30, stateful_step_count=40, deadline=None, derandomize=True)
