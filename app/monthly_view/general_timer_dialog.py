from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout,
)

from app.db.dao_tasks import list_tasks_for_timer


class GeneralTimerDialog(QDialog):
    """Wybór zadania dla stopera ogólnego (niezwiązanego z firmą)."""

    def __init__(self, pracownik_id: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Start stopera")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Co aktualnie robisz?"))
        form = QFormLayout()
        self.task_combo = QComboBox()
        self.task_combo.addItem("Zadania ogólne", None)
        for task in list_tasks_for_timer(pracownik_id, None):
            suffix = f" ({task['firma_nazwa']})" if task.get("firma_nazwa") else ""
            self.task_combo.addItem(f"{task['tytul']}{suffix}", task)
        self.description = QLineEdit()
        self.description.setPlaceholderText("opcjonalny opis, np. spotkanie, szkolenie")
        form.addRow("Zadanie:", self.task_combo)
        form.addRow("Opis:", self.description)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Start")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def activity(self) -> dict:
        task = self.task_combo.currentData()
        text = self.description.text().strip()
        if task:
            return {"task_id": task["id"], "task_title": text or task["tytul"]}
        return {"task_id": None, "task_title": text or "Zadania ogólne"}
