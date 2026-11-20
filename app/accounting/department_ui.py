"""Department navigation sharing the existing monthly editor and timer logic."""
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTabWidget, QLabel
from app.security.authorization import allowed_tabs


DEPARTMENT_LABELS = {'accounting': 'Księgowość', 'payroll': 'Kadry'}


def park_monthly_editor(editor, parent):
    """Own the shared editor without painting it over the main window on startup."""
    parking = QWidget(parent)
    parking.hide()
    layout = QVBoxLayout(parking)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(editor)
    editor.hide()
    return parking


def department_sections(permissions):
    return [label for label in ('Widok miesięczny', 'Sprawdzanie') if label in permissions]


def navigation_tabs(permissions):
    result = set(permissions) - {'Widok miesięczny', 'Sprawdzanie'}
    if department_sections(permissions):
        result.update(DEPARTMENT_LABELS.values())
    return result


class DepartmentPage(QWidget):
    def __init__(self, actor_id, department, monthly_page, parent=None, checking_factory=None):
        super().__init__(parent)
        if department not in DEPARTMENT_LABELS:
            raise ValueError('Nieprawidłowy dział.')
        self.actor_id, self.department, self.monthly_page = actor_id, department, monthly_page
        self.checking_page = None
        self._checking_factory = checking_factory
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self._sections = {}
        for title in department_sections(allowed_tabs(actor_id)):
            host = QWidget()
            host_layout = QVBoxLayout(host)
            host_layout.setContentsMargins(0, 0, 0, 0)
            self.tabs.addTab(host, title)
            self._sections[title] = host
        self.empty = QLabel('Brak dostępu do widoków tego działu.')
        layout.addWidget(self.empty)
        self.empty.setVisible(not self._sections)
        self.tabs.currentChanged.connect(self.refresh_async)

    def refresh_async(self, *_):
        host = self.tabs.currentWidget()
        title = self.tabs.tabText(self.tabs.currentIndex())
        if host is None or title not in allowed_tabs(self.actor_id):
            return
        if title == 'Widok miesięczny':
            # Reparent the same editor: no duplicate timers, pending edits or logic.
            if self.monthly_page.parentWidget() is not host:
                self.monthly_page.hide()
                host.layout().addWidget(self.monthly_page)
            changed = self.monthly_page.set_department(self.department)
            self.monthly_page.show()
            if not changed:
                self.monthly_page._reload_rows()
        else:
            if self.checking_page is None:
                if self._checking_factory is None:
                    from app.accounting.accounting_ui import AccountingPage
                    self.checking_page = AccountingPage(self.actor_id, department=self.department)
                else:
                    self.checking_page = self._checking_factory(self.actor_id, self.department)
                host.layout().addWidget(self.checking_page)
            self.checking_page.refresh_async()
