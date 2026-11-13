from PyQt6.QtWidgets import QDialog, QFormLayout, QLineEdit, QDialogButtonBox, QMessageBox
from app.employees.authority_profiles import FIELDS, load, save


class AuthorityProfileDialog(QDialog):
    def __init__(self, employee_id, actor_id, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Dane pracownika do PEL / UPL')
        self.employee_id, self.actor_id = employee_id, actor_id
        values = load(employee_id, actor_id)
        layout = QFormLayout(self)
        self.fields = {}
        for key, label in FIELDS.items():
            widget = QLineEdit(values.get(key, ''))
            self.fields[key] = widget
            layout.addRow(label, widget)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.persist)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def persist(self):
        try:
            save(self.employee_id, {k: w.text() for k, w in self.fields.items()}, self.actor_id)
        except (ValueError, PermissionError) as error:
            QMessageBox.warning(self, 'Dane pełnomocnika', str(error))
            return
        self.accept()
