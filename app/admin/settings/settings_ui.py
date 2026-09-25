# app/admin/settings/settings_ui.py
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTabWidget
from PyQt6.QtGui import QFont

from app.admin.settings.user_settings import UsersSettings
from app.admin.settings.roles_settings import RolesSettings
from app.admin.settings.email_settings import EmailSettings
from app.admin.settings.pricing_settings import PricingSettings
from app.admin.settings.pricing_kadry_settings import PricingKadrySettings
from app.admin.settings.extra_services_settings import ExtraServicesSettings
from app.admin.settings.companies_settings import CompaniesSettings
from app.admin.settings.logs_settings import LogsSettings
from app.admin.settings.system_settings import SystemSettings
from app.admin.settings.invoice_settings import InvoiceSettings
from app.admin.settings.database_settings import DatabaseSettings

from app.admin.global_settings_logic import GlobalSettingsLogic


class GlobalSettingsPage(QWidget):
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

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        if perms.get("can_manage_users"):
            t = UsersSettings()
            t.logic = self.logic
            t.pracownik_id = self.pracownik_id
            self.tabs.addTab(t, "Użytkownicy")

            r = RolesSettings()
            r.logic = self.logic
            r.pracownik_id = self.pracownik_id
            self.tabs.addTab(r, "Role")

        e = EmailSettings()
        e.logic = self.logic
        e.pracownik_id = self.pracownik_id
        self.tabs.addTab(e, "Konta email")

        if perms.get("can_view_invoices") or perms.get("can_access_admin_settings"):
            p = PricingSettings()
            p.logic = self.logic
            p.pracownik_id = self.pracownik_id
            self.tabs.addTab(p, "Cennik księgowość")

            pk = PricingKadrySettings()
            pk.logic = self.logic
            pk.pracownik_id = self.pracownik_id
            self.tabs.addTab(pk, "Cennik kadry")

        ex = ExtraServicesSettings()
        ex.logic = self.logic
        ex.pracownik_id = self.pracownik_id
        self.tabs.addTab(ex, "Usługi dodatkowe")

        comp = CompaniesSettings()
        comp.logic = self.logic
        comp.pracownik_id = self.pracownik_id
        self.tabs.addTab(comp, "Firmy")

        if perms.get("can_view_invoices"):
            inv = InvoiceSettings()
            inv.logic = self.logic
            inv.pracownik_id = self.pracownik_id
            self.tabs.addTab(inv, "Faktury")

        logs = LogsSettings()
        logs.logic = self.logic
        logs.pracownik_id = self.pracownik_id
        self.tabs.addTab(logs, "Logi")

        sys_tab = SystemSettings()
        sys_tab.logic = self.logic
        sys_tab.pracownik_id = self.pracownik_id
        self.tabs.addTab(sys_tab, "System")

        db_tab = DatabaseSettings()
        db_tab.logic = self.logic
        db_tab.pracownik_id = self.pracownik_id
        self.tabs.addTab(db_tab, "Baza danych")
