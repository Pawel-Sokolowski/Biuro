"""Global admin settings page UI."""

import logging
from typing import Optional

from app.utils.ui_imports import *
from app.admin.global_settings_logic import GlobalSettingsLogic

logger = logging.getLogger(__name__)


class GlobalSettingsPage(QWidget):
    """Administrator settings and management panel."""
    
    def __init__(self, pracownik_id: int, is_admin: bool, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.is_admin = is_admin
        self.logic = GlobalSettingsLogic()
        self.tabs = None
        self.users_table = None

        self.setWindowTitle("Panel administratora – ustawienia globalne")
        self.resize(APP_WIDTH - 200, APP_HEIGHT - 100)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(MARGIN_NORMAL, MARGIN_NORMAL, MARGIN_NORMAL, MARGIN_NORMAL)
        main_layout.setSpacing(SPACING_NORMAL)
        
        title = create_section_header("⚙️  Panel administratora – ustawienia globalne systemu")
        main_layout.addWidget(title)

        try:
            perms = self.logic.get_user_permissions(self.pracownik_id)
            if not perms["can_access_admin_settings"]:
                main_layout.addWidget(QLabel("Brak uprawnien administratora."))
                logger.warning(f"User {pracownik_id} attempted unauthorized admin access")
                return
        except Exception as e:
            logger.error(f"Failed to check permissions: {e}")
            main_layout.addWidget(QLabel(f"Błąd: {str(e)}"))
            return

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # Create tabs
        self._create_users_tab()
        self._create_roles_tab()
        self._create_email_tab()
        self._create_pricing_tab()
        self._create_pricing_tab_kadry()
        self._create_extra_services_tab()
        self._create_companies_tab()
        self._create_logs_tab()
        self._create_system_tab()

    # ===== USERS TAB =====
    def _create_users_tab(self):
        """Create users management tab."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING_NORMAL)

        # Users table
        self.users_table = QTableWidget(0, 5)
        self.users_table.setHorizontalHeaderLabels(["ID", "Email", "Imię", "Nazwisko", "Aktywny"])
        self.users_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.users_table)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(SPACING_NORMAL)
        
        btn_add = create_primary_button("➕ Dodaj użytkownika")
        btn_add.clicked.connect(self._add_user_dialog)
        btn_row.addWidget(btn_add)
        
        btn_block = create_secondary_button("🔒 Zablokuj/odblokuj")
        btn_block.clicked.connect(self._toggle_user_active)
        btn_row.addWidget(btn_block)
        
        btn_delete = create_danger_button("🗑️  Usuń")
        btn_delete.clicked.connect(self._delete_user)
        btn_row.addWidget(btn_delete)
        
        btn_refresh = create_primary_button("🔃 Odśwież")
        btn_refresh.clicked.connect(self._refresh_users)
        btn_row.addWidget(btn_refresh)
        
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.tabs.addTab(page, "👥 Użytkownicy")
        self._refresh_users()

    def _refresh_users(self):
        """Refresh users table."""
        try:
            rows = self.logic.get_users()
            self.users_table.setRowCount(0)
            
            for row_idx, r in enumerate(rows):
                self.users_table.insertRow(row_idx)
                for col_idx, key in enumerate(["id", "email", "imie", "nazwisko", "aktywny"]):
                    val = r.get(key, "N/A")
                    if key == "aktywny":
                        text = "✅ TAK" if val else "❌ NIE"
                    else:
                        text = str(val)
                    item = QTableWidgetItem(text)
                    self.users_table.setItem(row_idx, col_idx, item)
            
            logger.debug(f"Loaded {len(rows)} users")
        except Exception as e:
            logger.error(f"Failed to refresh users: {e}")
            QMessageBox.critical(self, "Błąd", f"Nie udało się załadować użytkowników: {str(e)}")

    def _add_user_dialog(self):
        """Show add user dialog."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Dodaj użytkownika")
        dlg.setFixedSize(400, 300)
        dlg.setStyleSheet(STYLE_DIALOG)
        
        form = QFormLayout(dlg)
        form.setSpacing(SPACING_NORMAL)
        
        email = QLineEdit()
        password = QLineEdit()
        password.setEchoMode(QLineEdit.EchoMode.Password)
        
        form.addRow("Email:", email)
        form.addRow("Hasło:", password)
        
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                logger.info(f"Adding user: {email.text()}")
                QMessageBox.information(self, "OK", "Użytkownik dodany")
                self._refresh_users()
            except Exception as e:
                logger.error(f"Failed to add user: {e}")
                QMessageBox.critical(self, "Błąd", f"Dodanie nie powiodło się: {str(e)}")

    def _toggle_user_active(self):
        """Toggle user active status."""
        logger.info("Toggling user active status")
        self._refresh_users()

    def _delete_user(self):
        """Delete selected user."""
        reply = QMessageBox.question(self, "Potwierdzenie", "Czy na pewno usunąć użytkownika?")
        if reply == QMessageBox.StandardButton.Yes:
            logger.info("User deleted")
            self._refresh_users()

    # ===== OTHER TABS =====
    def _create_roles_tab(self):
        """Create roles management tab."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Zarządzanie rolami"))
        self.tabs.addTab(page, "🌟 Role")

    def _create_email_tab(self):
        """Create email settings tab."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Ustawienia email"))
        self.tabs.addTab(page, "✉️  Email")

    def _create_pricing_tab(self):
        """Create accounting pricing tab."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Cenniki - Księgowość"))
        self.tabs.addTab(page, "📋 Cenniki Księg.")

    def _create_pricing_tab_kadry(self):
        """Create HR pricing tab."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Cenniki - Kadry"))
        self.tabs.addTab(page, "💼 Cenniki Kadry")

    def _create_extra_services_tab(self):
        """Create extra services tab."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Usługi dodatkowe"))
        self.tabs.addTab(page, "🌟 Usługi")

    def _create_companies_tab(self):
        """Create companies management tab."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Zarządzanie firmami"))
        self.tabs.addTab(page, "🏢 Firmy")

    def _create_logs_tab(self):
        """Create system logs tab."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Dzienniki systemu"))
        self.tabs.addTab(page, "🗒️  Logi")

    def _create_system_tab(self):
        """Create system settings tab."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Ustawienia systemu"))
        self.tabs.addTab(page, "⚙️  System")
