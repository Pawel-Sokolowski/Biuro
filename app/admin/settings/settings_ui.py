# app/admin/settings/settings_ui.py
from typing import Callable, List, Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)


def _scrollable(factory: Callable[[], QWidget]) -> Callable[[], QWidget]:
    def build() -> QWidget:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setWidget(factory())
        return area
    return build

from app.admin.global_settings_logic import GlobalSettingsLogic
from app.admin.settings.audit_settings import AuditSettings
from app.admin.settings.companies_settings import CompaniesSettings
from app.admin.settings.database_settings import DatabaseSettings
from app.admin.settings.email_settings import EmailSettings
from app.admin.settings.import_all_settings import ImportAllSettings
from app.admin.settings.extra_services_settings import ExtraServicesSettings
from app.admin.settings.invoice_settings import InvoiceSettings
from app.admin.settings.ksef_settings import KsefSettings
from app.admin.settings.logs_settings import LogsSettings
from app.admin.settings.pricing_kadry_settings import PricingKadrySettings
from app.admin.settings.pricing_settings import PricingSettings
from app.admin.settings.reset_calendar_settings import CalendarAndResetSettings
from app.admin.settings.roles_settings import RolesSettings
from app.admin.settings.user_settings import UsersSettings


class GlobalSettingsPage(QWidget):
    """Ustawienia pogrupowane w sekcje; lista po lewej, strony tworzone dopiero po otwarciu."""

    def __init__(self, pracownik_id: int, is_admin: bool, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.is_admin = is_admin
        self.logic = GlobalSettingsLogic()

        self.setWindowTitle("Panel administratora – ustawienia globalne")
        self.resize(1100, 800)

        main_layout = QVBoxLayout(self)
        title = QLabel("Panel administratora – ustawienia globalne systemu")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        main_layout.addWidget(title)

        perms = self.logic.get_user_permissions(self.pracownik_id)
        if not perms.get("can_access_admin_settings"):
            main_layout.addWidget(QLabel("Brak uprawnień administratora."))
            return

        body = QHBoxLayout()
        main_layout.addLayout(body)

        self.nav = QListWidget()
        self.nav.setFixedWidth(230)
        self.nav.setStyleSheet("QListWidget::item { padding: 6px 10px; }")
        self.stack = QStackedWidget()
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)

        self._factories: List[Optional[Callable[[], QWidget]]] = []
        self._built: List[Optional[QWidget]] = []

        can_view_invoices = perms.get("can_view_invoices") or perms.get("can_access_admin_settings")
        sections: List[Tuple[str, List[Tuple[str, Callable[[], QWidget], bool]]]] = [
            ("Użytkownicy i dostęp", [
                ("Użytkownicy", UsersSettings, bool(perms.get("can_manage_users"))),
                ("Role i uprawnienia", RolesSettings, bool(perms.get("can_manage_users"))),
            ]),
            ("Poczta i powiadomienia", [
                ("Konta e-mail", EmailSettings, True),
                ("Konto systemowe i kalendarz", _scrollable(CalendarAndResetSettings), True),
            ]),
            ("Cenniki i usługi", [
                ("Cennik księgowość", PricingSettings, bool(can_view_invoices)),
                ("Cennik kadry", PricingKadrySettings, bool(can_view_invoices)),
                ("Usługi dodatkowe", ExtraServicesSettings, True),
            ]),
            ("Firmy i faktury", [
                ("Firmy", lambda: CompaniesSettings(pracownik_id=self.pracownik_id), True),
                ("Faktury i KSeF", lambda: InvoiceSettings(pracownik_id=self.pracownik_id), bool(perms.get("can_view_invoices"))),
                ("KSeF – dostęp i certyfikat", KsefSettings, bool(perms.get("can_view_invoices"))),
            ]),
            ("System", [
                ("Import danych", ImportAllSettings, True),
                ("Baza danych i kopie", DatabaseSettings, True),
                ("Logi", LogsSettings, True),
                ("Audyt i importy", AuditSettings, True),
            ]),
        ]
        for section, pages in sections:
            visible = [page for page in pages if page[2]]
            if not visible:
                continue
            self._add_section(section)
            for name, factory, _ in visible:
                self._add_page(name, factory)

        self.nav.currentRowChanged.connect(self._show_page)
        first_page = next((row for row, factory in enumerate(self._factories) if factory), -1)
        self.nav.setCurrentRow(first_page)

    def _add_section(self, name: str):
        item = QListWidgetItem(name.upper())
        font = item.font()
        font.setBold(True)
        font.setPointSize(max(font.pointSize() - 1, 8))
        item.setFont(font)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.nav.addItem(item)
        self._factories.append(None)
        self._built.append(None)
        self.stack.addWidget(QWidget())

    def _add_page(self, name: str, factory: Callable[[], QWidget]):
        self.nav.addItem(QListWidgetItem("   " + name))
        self._factories.append(factory)
        self._built.append(None)
        self.stack.addWidget(QWidget())

    def _show_page(self, row: int):
        factory = self._factories[row] if 0 <= row < len(self._factories) else None
        if factory is None:
            return
        if self._built[row] is None:
            page = factory()
            page.logic = self.logic
            page.pracownik_id = self.pracownik_id
            placeholder = self.stack.widget(row)
            self.stack.removeWidget(placeholder)
            placeholder.deleteLater()
            self.stack.insertWidget(row, page)
            self._built[row] = page
        self.stack.setCurrentIndex(row)
