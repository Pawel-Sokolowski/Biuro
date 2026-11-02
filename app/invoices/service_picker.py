"""Select a catalogue service without modifying the activity register."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QVBoxLayout)
from app.db.dao_global_settings import get_extra_services
from app.invoices.positions import normalize_position


class ServicePicker(QDialog):
    def __init__(self, services, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dodaj usługę z katalogu")
        self.resize(620, 450)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Szukaj po nazwie lub kategorii…")
        layout.addWidget(self.search)
        self.services = QListWidget()
        layout.addWidget(self.services)
        for service in services:
            service = dict(service)
            label = f"{service['nazwa']}  ·  {service['cena_netto']:.2f} zł netto  ·  VAT {service['stawka_vat']}"
            if service.get("kategoria"):
                label += f"  ·  {service['kategoria']}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, service)
            self.services.addItem(item)
        if not self.services.count():
            empty = QLabel("Katalog jest pusty. Administrator może dodać usługi w Ustawienia → Usługi dodatkowe.")
            empty.setWordWrap(True)
            layout.addWidget(empty)
        form = QFormLayout()
        self.quantity = QDoubleSpinBox()
        self.quantity.setRange(0.001, 1000000)
        self.quantity.setDecimals(3)
        self.quantity.setValue(1)
        form.addRow("Ilość:", self.quantity)
        layout.addLayout(form)
        self.total = QLabel("Wybierz usługę, aby zobaczyć wartość pozycji.")
        self.total.setWordWrap(True)
        layout.addWidget(self.total)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.add_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.add_button.setText("Dodaj do faktury")
        self.add_button.setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Anuluj")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self._filter)
        self.services.currentItemChanged.connect(lambda current, previous: self.add_button.setEnabled(current is not None and not current.isHidden()))
        self.services.currentItemChanged.connect(self._update_total)
        self.quantity.valueChanged.connect(self._update_total)
        self.services.itemDoubleClicked.connect(lambda item: self.accept())

    def _filter(self, text):
        for row in range(self.services.count()):
            item = self.services.item(row)
            item.setHidden(text.casefold() not in item.text().casefold())
        current = self.services.currentItem()
        self.add_button.setEnabled(current is not None and not current.isHidden())
        self._update_total()

    def _update_total(self, *_args):
        position = self.position()
        self.total.setText(
            f"Wartość netto: {position['netto']:.2f} zł  ·  brutto: {position['brutto']:.2f} zł"
            if position else "Wybierz usługę, aby zobaczyć wartość pozycji.")

    def position(self):
        item = self.services.currentItem()
        if item is None or item.isHidden():
            return None
        service = item.data(Qt.ItemDataRole.UserRole)
        return normalize_position({"nazwa": service["nazwa"], "ilosc": self.quantity.value(),
                                   "jednostka": "szt.", "cena_netto": service["cena_netto"],
                                   "vat": service["stawka_vat"], "kategoria": service.get("kategoria", ""),
                                   "generated": False})


def choose_service(parent):
    try:
        services = get_extra_services()
    except Exception:
        QMessageBox.warning(parent, "Katalog usług", "Nie udało się pobrać katalogu usług. Sprawdź połączenie z bazą i spróbuj ponownie.")
        return None
    dialog = ServicePicker(services, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.position()
    return None
