from PyQt6.QtCore import QDateTime
from PyQt6.QtWidgets import (
    QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox, QFormLayout,
    QLabel, QLineEdit, QMessageBox, QSpinBox, QVBoxLayout,
)

from app.db.dao_employees import list_employees
from app.db.dao_timers import DEPARTMENTS, add_manual_time, list_extra_service_catalogue
from app.monthly_view.month_picker import MonthPicker
from app.utils.months import default_month_for_employee
from app.remote.screen_data import company_identities


class ManualTimeDialog(QDialog):
    def __init__(self, actor_id: int, parent=None, *, company_id=None, employee_id=None):
        super().__init__(parent)
        self.actor_id = actor_id
        self.setWindowTitle('Dodaj czas ręcznie')
        self.resize(520, 360)

        layout = QVBoxLayout(self)
        note = QLabel('Wpis zostanie zapisany dla wybranego pracownika. Historia zmian zachowa osobę, która dodała wpis.')
        note.setWordWrap(True)
        layout.addWidget(note)

        form = QFormLayout()
        self.employee = QComboBox()
        employees = [row for row in list_employees() if row.get('aktywny', 1)]
        employees.sort(key=lambda row: ((row.get('nazwisko') or '').casefold(), (row.get('imie') or '').casefold()))
        for row in employees:
            self.employee.addItem(f"{row.get('imie') or ''} {row.get('nazwisko') or ''}".strip(), row['id'])
        if employee_id is not None:
            self.employee.setCurrentIndex(max(0, self.employee.findData(employee_id)))

        self.company = QComboBox()
        self.company.addItem('Bez firmy', None)
        companies = list(company_identities())
        companies.sort(key=lambda row: (row.get('nazwa') or '').casefold())
        for row in companies:
            self.company.addItem(row.get('nazwa') or str(row['id']), row['id'])
        if company_id is not None:
            self.company.setCurrentIndex(max(0, self.company.findData(company_id)))

        self.department = QComboBox()
        for key, label in DEPARTMENTS:
            self.department.addItem(label, key)

        self.month = MonthPicker(default_month_for_employee(employee_id or actor_id))
        self.service = QComboBox()
        self.service.addItem('Wybierz usługę dodatkową', None)
        for row in list_extra_service_catalogue():
            self.service.addItem(row['nazwa'], row['id'])

        self.started = QDateTimeEdit(QDateTime.currentDateTime())
        self.started.setCalendarPopup(True)
        self.started.setDisplayFormat('dd.MM.yyyy HH:mm')
        self.hours = QSpinBox()
        self.hours.setRange(0, 999)
        self.minutes = QSpinBox()
        self.minutes.setRange(0, 59)
        self.description = QLineEdit()

        form.addRow('Miesiąc rozliczeniowy', self.month)
        form.addRow('Pracownik', self.employee)
        form.addRow('Firma', self.company)
        form.addRow('Dział', self.department)
        form.addRow('Usługa dodatkowa', self.service)
        form.addRow('Rozpoczęcie', self.started)
        form.addRow('Godziny', self.hours)
        form.addRow('Minuty', self.minutes)
        form.addRow('Opis', self.description)
        layout.addLayout(form)
        self.employee.currentIndexChanged.connect(self._employee_changed)

        self.department.currentIndexChanged.connect(self._update_service)
        self._update_service()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_service(self):
        enabled = self.department.currentData() == 'dodatkowe'
        self.service.setEnabled(enabled)
        if not enabled:
            self.service.setCurrentIndex(0)

    def save(self):
        seconds = self.hours.value() * 3600 + self.minutes.value() * 60
    def _employee_changed(self):
        self.month.setCurrentText(default_month_for_employee(self.employee.currentData()))

        try:
            add_manual_time(
                self.employee.currentData(),
                self.company.currentData(),
                self.department.currentData(),
                self.description.text(),
                seconds,
                self.actor_id,
                self.service.currentData(),
                started_at=self.started.dateTime().toString('yyyy-MM-ddTHH:mm:ss'),
            )
        except (ValueError, PermissionError) as error:
            QMessageBox.warning(self, 'Dodaj czas', str(error))
            return
        self.accept()
                miesiac=self.month.monthText(),
