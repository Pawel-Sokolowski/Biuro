# app/admin/settings/companies_settings.py
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QPushButton, QHeaderView, QMessageBox, QHBoxLayout, QDialog,
    QVBoxLayout as QVBoxLayoutDialog, QFileDialog, QListWidget,
    QListWidgetItem, QLineEdit, QFormLayout, QProgressDialog
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import QThread, pyqtSignal, Qt

from app.admin.global_settings_logic import GlobalSettingsLogic
from app.companies.company_form import CompanyForm
from app.utils.importers import (
    import_companies_from_excel_file_payloads,
    import_companies_fast_from_excel_file,
    import_tax_offices_from_excel,
)


# ============================================================
# WORKER — IMPORT PAYLOADÓW (NIE zapisuje do DB)
# ============================================================
class ImportWorker(QThread):
    finished_with_result = pyqtSignal(object, name="finished_with_result")
    error = pyqtSignal(str, name="error")

    def __init__(self, filepath: str):
        super().__init__()
        self.filepath = filepath

    def run(self):
        try:
            payloads, warnings = import_companies_from_excel_file_payloads(self.filepath)
            self.finished_with_result.emit((payloads, warnings))
        except Exception as e:
            self.error.emit(str(e))


class FastImportWorker(QThread):
    finished_with_result = pyqtSignal(object, name="finished_with_result")
    error = pyqtSignal(str, name="error")

    def __init__(self, filepath: str):
        super().__init__()
        self.filepath = filepath

    def run(self):
        try:
            self.finished_with_result.emit(import_companies_fast_from_excel_file(self.filepath))
        except Exception as e:
            self.error.emit(str(e))


# ============================================================
# COMPANIES SETTINGS — PANEL ADMINA
# ============================================================
class CompaniesSettings(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.logic = GlobalSettingsLogic()

        layout = QVBoxLayout(self)

        title = QLabel("Firmy")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        # Tabela firm
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["ID", "Nazwa", "NIP", "Miasto"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        # Przyciski
        btn_row = QHBoxLayout()

        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self._refresh)
        btn_row.addWidget(btn_refresh)

        btn_add = QPushButton("Dodaj nową")
        btn_add.clicked.connect(self._add_company)
        btn_row.addWidget(btn_add)

        btn_edit = QPushButton("Edytuj wybraną")
        btn_edit.clicked.connect(self._edit_selected)
        btn_row.addWidget(btn_edit)

        btn_delete = QPushButton("Usuń wybraną")
        btn_delete.clicked.connect(self._delete_selected)
        btn_row.addWidget(btn_delete)

        btn_import = QPushButton("Importuj z pliku (weryfikacja)")
        btn_import.clicked.connect(self._import_from_file)
        btn_row.addWidget(btn_import)

        btn_fast_import = QPushButton("Importuj bez weryfikacji")
        btn_fast_import.clicked.connect(self._import_without_verification)
        btn_row.addWidget(btn_fast_import)

        btn_tax_offices = QPushButton("Importuj urzędy skarbowe")
        btn_tax_offices.clicked.connect(self._import_tax_offices)
        btn_row.addWidget(btn_tax_offices)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._refresh()

        self._current_worker = None
        self._progress_dialog = None

    # ============================================================
    # TABELA — ODSWIEŻANIE
    # ============================================================
    def _refresh(self):
        rows = self.logic.get_companies()
        self.table.setRowCount(0)

        for f in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)

            def get_field(obj, key, default=""):
                if isinstance(obj, dict):
                    return obj.get(key, default)
                try:
                    return obj[key]
                except Exception:
                    return default

            fid = get_field(f, "id", "")
            nazwa = get_field(f, "nazwa", "")
            nip = get_field(f, "nip", "")
            miasto = get_field(f, "miasto", "")

            self.table.setItem(row, 0, QTableWidgetItem(str(fid)))
            self.table.setItem(row, 1, QTableWidgetItem(nazwa))
            self.table.setItem(row, 2, QTableWidgetItem(nip))
            self.table.setItem(row, 3, QTableWidgetItem(miasto))

    # ============================================================
    # POBIERANIE ID ZAZNACZONEJ FIRMY
    # ============================================================
    def _get_selected_id(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if not item:
            return None
        try:
            return int(item.text())
        except Exception:
            return None

    # ============================================================
    # DODAWANIE FIRMY
    # ============================================================
    def _add_company(self):
        CompanyForm(None, self, actor_id=self.pracownik_id).exec()
        self._refresh()

    # ============================================================
    # EDYCJA FIRMY
    # ============================================================
    def _edit_selected(self):
        fid = self._get_selected_id()
        if fid is None:
            QMessageBox.information(self, "Firmy", "Wybierz firmę.")
            return

        firma = self.logic.get_company(fid)
        if not firma:
            QMessageBox.critical(self, "Błąd", "Nie znaleziono firmy.")
            return

        CompanyForm(
            firma["id"],
            self,
            actor_id=self.pracownik_id,
        ).exec()
        self._refresh()

    # ============================================================
    # USUWANIE FIRMY
    # ============================================================
    def _delete_selected(self):
        fid = self._get_selected_id()
        if fid is None:
            QMessageBox.information(self, "Firmy", "Wybierz firmę.")
            return

        ret = QMessageBox.question(
            self,
            "Usuń firmę",
            f"Czy na pewno chcesz usunąć firmę ID={fid}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        try:
            self.logic.delete_company(fid)
            self._refresh()
            QMessageBox.information(self, "OK", "Usunięto firmę.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się usunąć firmy: {e}")

    # ============================================================
    # IMPORT — URUCHOMIENIE WORKERA
    # ============================================================
    def _import_from_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Wybierz plik do importu",
            "",
            "Excel (*.xlsx *.xls);;CSV (*.csv);;Wszystkie pliki (*)"
        )
        if not path:
            return

        worker = ImportWorker(path)
        worker.finished_with_result.connect(self._on_import_finished)
        worker.error.connect(self._on_import_error)
        worker.start()

        self._current_worker = worker

    def _import_tax_offices(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Wybierz plik urzędów skarbowych",
            "",
            "Excel (*.xlsx *.xls);;CSV (*.csv);;Wszystkie pliki (*)",
        )
        if not path:
            return
        try:
            count = import_tax_offices_from_excel(path)
            QMessageBox.information(
                self,
                "Import urzędów",
                f"Zaimportowano lub zaktualizowano {count} urzędów skarbowych.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Błąd importu urzędów", str(exc))

    def _import_without_verification(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Wybierz plik do szybkiego importu",
            "",
            "Excel (*.xlsx *.xls);;CSV (*.csv);;Wszystkie pliki (*)",
        )
        if not path:
            return
        worker = FastImportWorker(path)
        worker.finished_with_result.connect(self._on_fast_import_finished)
        worker.error.connect(self._on_import_error)
        worker.start()
        self._current_worker = worker

    def _on_fast_import_finished(self, result):
        if result.get("warnings"):
            QMessageBox.warning(self, "Ostrzeżenia importu", "\n".join(result["warnings"]))
        QMessageBox.information(
            self,
            "Import zakończony",
            f"Utworzono: {result.get('created', 0)}\n"
            f"Zaktualizowano: {result.get('updated', 0)}",
        )
        self._refresh()

    def _on_import_error(self, msg):
        QMessageBox.critical(self, "Błąd importu", msg)

    # ============================================================
    # IMPORT — ZAKOŃCZENIE
    # ============================================================
    def _on_import_finished(self, result):
        payloads, warnings = result
        if warnings:
            QMessageBox.information(self, "Ostrzeżenia importu", "\n".join(warnings))
        self._run_import_wizard(payloads)
    # ============================================================
    # WIZARD — URUCHOMIENIE
    # ============================================================
    def _run_import_wizard(self, payloads):
        from app.utils.import_wizard import ImportWizard

        wizard = ImportWizard(payloads, self, actor_id=self.pracownik_id)
        wizard.exec()

        self._refresh()

    # ============================================================
    # OSTRZEŻENIA — WYCIĄGANIE NIERZOPZNANYCH PRACOWNIKÓW
    # ============================================================
    def _extract_unresolved_from_warnings(self, warnings):
        unresolved = []
        for w in warnings:
            try:
                if "nie znaleziono pracowników" in w:
                    parts = w.split(":", 1)
                    tail = parts[1] if len(parts) > 1 else w
                    idx = tail.find("nie znaleziono pracowników")
                    if idx != -1:
                        names_part = tail[idx + len("nie znaleziono pracowników"):].strip(" .:")
                        names = [n.strip() for n in names_part.split(",") if n.strip()]
                        for n in names:
                            if n not in unresolved:
                                unresolved.append(n)
            except Exception:
                continue
        return unresolved

    # ============================================================
    # DIALOG DODAWANIA NIEROZPOZNANYCH PRACOWNIKÓW
    # ============================================================
    def _handle_unresolved_employees(self, unresolved):
        dlg = QDialog(self)
        dlg.setWindowTitle("Nierozpoznani pracownicy")
        lay = QVBoxLayoutDialog(dlg)

        info = QLabel("Nie znaleziono następujących pracowników. Zaznacz tych, których chcesz dodać do systemu:")
        lay.addWidget(info)

        list_widget = QListWidget()
        for name in unresolved:
            item = QListWidgetItem(name)
            item.setSelected(True)
            list_widget.addItem(item)
        lay.addWidget(list_widget)

        btn_row = QHBoxLayout()
        btn_add_selected = QPushButton("Dodaj zaznaczonych")
        btn_cancel = QPushButton("Anuluj")
        btn_row.addWidget(btn_add_selected)
        btn_row.addWidget(btn_cancel)
        lay.addLayout(btn_row)

        def on_cancel():
            dlg.reject()

        def on_add_selected():
            selected = [list_widget.item(i).text() for i in range(list_widget.count()) if list_widget.item(i).isSelected()]
            if not selected:
                QMessageBox.information(self, "Dodawanie pracowników", "Nie wybrano żadnego pracownika.")
                return

            added = []
            failed = []
            if hasattr(self.logic, "add_employee"):
                for name in selected:
                    try:
                        res = self.logic.add_employee(name)
                        added.append(name)
                    except Exception as e:
                        failed.append(f"{name} ({e})")
            else:
                self._manual_add_employees(selected)
                dlg.accept()
                return

            summary = []
            if added:
                summary.append("Dodano: " + ", ".join(added))
            if failed:
                summary.append("Nie udało się dodać: " + ", ".join(failed))
            QMessageBox.information(self, "Wynik dodawania", "\n".join(summary))
            dlg.accept()

        btn_cancel.clicked.connect(on_cancel)
        btn_add_selected.clicked.connect(on_add_selected)

        dlg.exec()

    # ============================================================
    # RĘCZNE DODAWANIE PRACOWNIKÓw
    # ============================================================
    def _manual_add_employees(self, names):
        dlg = QDialog(self)
        dlg.setWindowTitle("Ręczne dodawanie pracowników")
        lay = QVBoxLayoutDialog(dlg)

        info = QLabel("Twoja logika nie udostępnia metody add_employee.\nWprowadź dane ręcznie i zapisz, a następnie dodaj pracowników w module pracowników.")
        lay.addWidget(info)

        form_layout = QFormLayout()
        name_edit = QLineEdit()
        email_edit = QLineEdit()
        form_layout.addRow("Imię i nazwisko", name_edit)
        form_layout.addRow("Email (opcjonalnie)", email_edit)
        lay.addLayout(form_layout)

        if names:
            name_edit.setText(names[0])

        btn_row = QHBoxLayout()
        btn_save = QPushButton("Zapisz (tylko lokalnie)")
        btn_close = QPushButton("Zamknij")
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_close)
        lay.addLayout(btn_row)

        def on_close():
            dlg.reject()

        def on_save():
            n = name_edit.text().strip()
            e = email_edit.text().strip()
            if not n:
                QMessageBox.information(self, "Brak danych", "Wprowadź imię i nazwisko pracownika.")
                return
            QMessageBox.information(self, "Dane pracownika",
                                    f"Pracownik do dodania:\nImię i nazwisko: {n}\nEmail: {e}\n\n"
                                    "Aby dodać go do systemu, użyj modułu pracowników lub zaimplementuj metodę add_employee w GlobalSettingsLogic.")
            dlg.accept()

        btn_close.clicked.connect(on_close)
        btn_save.clicked.connect(on_save)

        dlg.exec()
