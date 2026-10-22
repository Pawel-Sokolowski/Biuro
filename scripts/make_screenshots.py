"""Generuje zrzuty ekranu do README na tymczasowym schemacie z danymi fikcyjnymi.

Uruchomienie: python scripts\\make_screenshots.py
Prawdziwe dane firm nie są odczytywane - schemat jest tworzony i usuwany.
"""
import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication(["biuro-screenshots"])

from app.config import get_schema  # noqa: E402
from app.db.dao_companies import assign_employee_to_company, save_company  # noqa: E402
from app.db.schema import init_db  # noqa: E402
from app.security.bootstrap import create_initial_admin  # noqa: E402
from tests.base import create_test_schema, drop_test_schema  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
PAGES = [
    ("pulpit", 0), ("widok-miesieczny", 1), ("klienci", 2), ("faktury", 5),
    ("raporty", 6), ("zadania", 7), ("kalendarz", 8), ("ustawienia", 9),
]
DEMO_COMPANIES = [
    ("Przykładowa Firma Alfa Sp. z o.o.", "Alfa", "1234563218", "Szczecin"),
    ("Demo Usługi Beta S.C.", "Beta", "5260001246", "Gdańsk"),
    ("Jan Przykładowy Gamma", "Gamma", "7740001454", "Poznań"),
    ("Wzorcowa Delta Sp. z o.o.", "Delta", "9512345632", "Wrocław"),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    previous = get_schema()
    schema = create_test_schema()
    try:
        init_db()
        admin_id = create_initial_admin("admin@example.com", "Admin12345", "Jan", "Demo")
        emp = admin_id
        for nazwa, skrot, nip, miasto in DEMO_COMPANIES:
            firma_id = save_company({"nazwa": nazwa, "nazwa_skrocona": skrot, "nip": nip, "miasto": miasto})
            assign_employee_to_company(emp, firma_id)
        for name in ("warning", "information", "critical", "question"):
            mock.patch.object(QMessageBox, name, return_value=QMessageBox.StandardButton.Yes).start()
        from app.main_ui import OfficeManagerApp
        win = OfficeManagerApp(emp)
        win.resize(1400, 850)
        win.show()
        for name, index in PAGES:
            win.sidebar.setCurrentRow(index)
            win.pages.setCurrentIndex(index)
            win._refresh_current_page(index)
            page = win.pages.widget(index)
            for meth in ("_refresh", "refresh", "_search"):
                if hasattr(page, meth):
                    getattr(page, meth)()
                    break
            app.processEvents()
            win.grab().save(str(OUT / f"{name}.png"))
            print("OK", name)
        win.pages.setCurrentIndex(9)
        win._refresh_current_page(9)
        settings = win.page_settings
        skip = {"Logi"}
        for row, factory in enumerate(settings._factories):
            if factory is None:
                continue
            title = settings.nav.item(row).text().strip()
            if title in skip:
                continue
            settings.nav.setCurrentRow(row)
            app.processEvents()
            from PyQt6.QtWidgets import QLabel
            for label in settings.stack.currentWidget().findChildren(QLabel):
                if label.text().startswith("PostgreSQL:"):
                    label.setText("PostgreSQL: localhost:5432/biuro")
            slug = title.lower().translate(str.maketrans("ąćęłńóśźż –", "acelnoszz--")).replace("--", "-")
            win.grab().save(str(OUT / f"admin-{slug}.png"))
            print("OK admin", title)
        win.close()
    finally:
        drop_test_schema(schema, previous)
    return 0


if __name__ == "__main__":
    sys.exit(main())
