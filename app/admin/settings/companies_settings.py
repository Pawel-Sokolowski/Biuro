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
from app.utils.importers import import_companies_from_excel_file, import_companies_fast_from_excel_file


class ImportWorker(QThread):
    """
    Wątek wykonujący import pliku, żeby nie blokować GUI.
    Emituje sygnał finished_with_result po zakończeniu.
    """
    finished_with_result = pyqtSignal(dict, name="finished_with_result")
    error = pyqtSignal(str, name="error")
    progress = pyqtSignal(int, name="progress")

    def __init__(self, filepath: str, db_path: str = None, use_fast: bool = True):
        super().__init__()
        self.filepath = filepath
        self.db_path = db_path
        self.use_fast = use_fast

    def run(self):
        try:
            if self.db_path:
                res = import_companies_from_excel_file(self.filepath, db_path=self.db_path)
            else:
                res = import_companies_from_excel_file(self.filepath)
            # Upewnij się, że res jest dict
            if not isinstance(res, dict):
                res = {"created": 0, "updated": 0, "warnings": [f"Nieoczekiwany wynik importera: {res}"]}
            self.finished_with_result.emit(res)
        except Exception as e:
            self.error.emit(str(e))


class CompaniesSettings(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.logic = GlobalSettingsLogic()

        layout = QVBoxLayout(self)

        title = QLabel("Firmy")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["ID", "Nazwa", "NIP", "Miasto"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

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

        # Przyciski importu
        btn_import = QPushButton("Importuj z pliku")
        btn_import.clicked.connect(self._import_from_file)
        btn_row.addWidget(btn_import)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._refresh()

        # Przechowujemy referencję do workerów, żeby nie były GC'owane
        self._current_worker = None
        self._progress_dialog = None

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

    def _add_company(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Dodaj firmę")
        lay = QVBoxLayoutDialog(dlg)
        # Uwaga: CompanyForm może oczekiwać parent jako pierwszy argument — używamy pozycyjnego dlg
        form = CompanyForm(dlg, pracownik_id=0, company_data=None)
        lay.addWidget(form)

        def on_saved():
            self._refresh()
        dlg.on_saved = on_saved

        dlg.exec()

    def _edit_selected(self):
        fid = self._get_selected_id()
        if fid is None:
            QMessageBox.information(self, "Firmy", "Wybierz firmę.")
            return

        firma = self.logic.get_company(fid)
        if not firma:
            QMessageBox.critical(self, "Błąd", "Nie znaleziono firmy.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Edytuj firmę")
        lay = QVBoxLayoutDialog(dlg)
        # Uwaga: CompanyForm może oczekiwać parent jako pierwszy argument — używamy pozycyjnego dlg
        form = CompanyForm(dlg, pracownik_id=0, company_data=firma)
        lay.addWidget(form)

        def on_saved():
            self._refresh()
        dlg.on_saved = on_saved

        dlg.exec()

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

    def _import_from_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Wybierz plik do importu",
            "",
            "Excel (*.xlsx *.xls);;CSV (*.csv);;Wszystkie pliki (*)"
        )
        if not path:
            return

        # Pokaż dialog postępu z zakresem (0..100)
        self._progress_dialog = QProgressDialog("Importuję plik...", "", 0, 100, self)
        self._progress_dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._progress_dialog.setCancelButton(None)
        self._progress_dialog.setMinimumDuration(200)
        self._progress_dialog.setValue(0)
        self._progress_dialog.show()

        # Uruchom worker w tle (domyślnie szybki importer)
        worker = ImportWorker(path, use_fast=True)
        worker.finished_with_result.connect(self._on_import_finished)
        worker.error.connect(self._on_import_error)
        worker.finished.connect(self._cleanup_worker)
        worker.progress.connect(self._on_import_progress)
        worker.start()

        # Trzymaj referencję
        self._current_worker = worker

    def _cleanup_worker(self):
        # zamknij progress i usuń referencję
        if self._progress_dialog:
            try:
                self._progress_dialog.close()
            except Exception:
                pass
            self._progress_dialog = None
        self._current_worker = None

    def _on_import_progress(self, value: int):
        if self._progress_dialog:
            try:
                self._progress_dialog.setValue(max(0, min(100, value)))
            except Exception:
                pass
    def _on_import_error(self, err_msg: str):
        self._cleanup_worker()
        QMessageBox.critical(self, "Błąd importu", f"Wystąpił błąd podczas importu:\n{err_msg}")

    def _on_import_finished(self, result: dict):
        self._cleanup_worker()

        created = result.get("created", 0)
        updated = result.get("updated", 0)
        warnings = result.get("warnings", []) or []

        msg_lines = [f"Utworzono: {created}", f"Zaktualizowano: {updated}"]
        if warnings:
            msg_lines.append("")
            msg_lines.append("Ostrzeżenia:")
            msg_lines.extend(warnings)

        QMessageBox.information(self, "Wynik importu", "\n".join(msg_lines))

        # Wyciągnij nierozpoznanych pracowników i zaproponuj dodanie
        unresolved = self._extract_unresolved_from_warnings(warnings)
        if unresolved:
            self._handle_unresolved_employees(unresolved)

        self._refresh()

    def _extract_unresolved_from_warnings(self, warnings):
        """
        Szuka w ostrzeżeniach fragmentów typu:
        "Wiersz X: nie znaleziono pracowników A, B, C."
        i zwraca listę unikalnych nazw pracowników.
        """
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

    def _handle_unresolved_employees(self, unresolved):
        """
        Pokazuje dialog z listą nierozpoznanych pracowników i opcją dodania ich.
        Próbuje wywołać self.logic.add_employee(name) dla wybranych pozycji.
        Jeśli metoda nie istnieje, dialog pozwala na ręczne wprowadzenie.
        """
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
                # Brak metody - pokaż prostą formę dodawania ręcznego
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

    def _manual_add_employees(self, names):
        """
        Prosty dialog do ręcznego dodania pracownika, jeśli logic.add_employee nie istnieje.
        Pozwala wprowadzić imię/nazwisko i opcjonalnie email dla pierwszego z listy.
        """
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
