"""Kreator importu wszystkich danych krok po kroku: cennik, urzędy, firmy, cechy firm, czasomierz, faktury."""
import datetime
import logging

from PyQt6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

FILTER = "Pliki danych (*.xlsx *.xls *.csv);;Wszystkie pliki (*)"
FILTER_XLSX = "Pliki Excel (*.xlsx)"


def _import_cennik(path, parent):
    from app.admin.global_settings_logic import GlobalSettingsLogic
    before = _count("cenniki")
    GlobalSettingsLogic().import_global_pricing_from_csv(path)
    after = _count("cenniki")
    return f"zaimportowano cennik (pozycji w cenniku: {after}, nowych: {after - before})"


def _count(table):
    from app.db.dao_conn import get_conn
    conn = get_conn()
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def _import_urzedy(path, parent):
    from app.utils.importers import import_tax_offices_from_excel
    return f"zaimportowano urzędów skarbowych: {import_tax_offices_from_excel(path)}"


def _import_firmy(path, parent):
    from PyQt6.QtWidgets import QApplication, QProgressDialog
    from PyQt6.QtCore import Qt
    from app.utils.importers import import_companies_fast_from_excel_file

    dialog = QProgressDialog("Przygotowanie importu...", None, 0, 0, parent)
    dialog.setWindowTitle("Import firm")
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumWidth(420)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.show()

    def progress(text):
        dialog.setLabelText(text)
        QApplication.processEvents()

    try:
        r = import_companies_fast_from_excel_file(path, progress=progress)
    finally:
        dialog.close()
    text = f"zaimportowano firm: {r.get('created', 0) + r.get('updated', 0)} (nowych: {r.get('created', 0)}, zaktualizowanych: {r.get('updated', 0)})"
    if r.get("warnings"):
        text += f"\nOstrzeżeń: {len(r['warnings'])}\n\n" + "\n".join(r["warnings"][:15])
    return text


def _import_cechy(path, parent):
    from app.utils.import_assignments import import_role_assignments_from_file
    r = import_role_assignments_from_file(path)
    text = f"zaktualizowano firm: {r.get('updated', 0)}"
    if r.get("warnings"):
        text += f", ostrzeżeń: {len(r['warnings'])}"
    return text


def _import_czas(path, parent):
    from app.reports import import_dao
    r = import_dao.import_work_file(path)
    return f"dodano {r['dodane']}, pominięto {r['pominiete']}, bez firmy {r['bez_firmy']}"


def _import_faktury(path, parent):
    from app.reports import import_dao
    year = max(datetime.date.today().year, 2026)
    months = [f"{y}-{m:02d}" for y in range(2026, year + 1) for m in range(1, 13)]
    month, ok = QInputDialog.getItem(
        parent, "Miesiąc faktur",
        "Miesiąc dla faktur, w których numerze nie ma miesiąca (np. FV/0001/01/2026):",
        months, 0, False,
    )
    if not ok:
        raise InterruptedError
    overwrite = QMessageBox.question(
        parent, "Faktury",
        "Nadpisać istniejące ilości dokumentów w widoku miesięcznym?",
    ) == QMessageBox.StandardButton.Yes
    r = import_dao.import_invoices_file(path, month, overwrite)
    return (f"dodano {r['dodane']}, pominięto {r['pominiete']}, bez firmy {r['bez_firmy']}, "
            f"uzupełniono ilość dokumentów: {r.get('ilosc_dok', 0)}")


STEPS = [
    ("cennikiem", "cennik", FILTER, _import_cennik),
    ("urzędami skarbowymi", "urzędy", FILTER, _import_urzedy),
    ("firmami", "firmy", FILTER, _import_firmy),
    ("cechami firm (przypisania pracowników)", "cechy firm", FILTER, _import_cechy),
    ("czasomierzem", "czasomierz", FILTER_XLSX, _import_czas),
    ("fakturami", "faktury", FILTER_XLSX, _import_faktury),
]


def run_import_wizard(parent=None) -> list:
    """Prowadzi użytkownika przez kolejne importy. Zwraca listę wyników."""
    total = len(STEPS)
    results = []
    for number, (what, label, file_filter, handler) in enumerate(STEPS, start=1):
        path, _ = QFileDialog.getOpenFileName(
            parent, f"Krok {number} z {total}: wybierz plik z {what}", "", file_filter
        )
        if not path:
            box = QMessageBox(parent)
            box.setWindowTitle(f"Krok {number} z {total}")
            box.setText(f"Nie wybrano pliku z {what}.")
            btn_skip = box.addButton("Pomiń ten krok", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Przerwij import", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is btn_skip:
                results.append(f"{label}: pominięto")
                continue
            results.append("Przerwano.")
            break
        try:
            message = handler(path, parent)
            results.append(f"{label}: {message}")
            QMessageBox.information(parent, f"Krok {number} z {total}: {label}", message[:1].upper() + message[1:])
        except InterruptedError:
            results.append(f"{label}: pominięto")
        except Exception as exc:
            logging.getLogger(__name__).exception("Import %s nie powiódł się", label)
            results.append(f"{label}: BŁĄD — {exc}")
            if QMessageBox.question(
                parent, "Błąd importu", f"{label}: {exc}\n\nKontynuować kolejne kroki?"
            ) != QMessageBox.StandardButton.Yes:
                break
    QMessageBox.information(parent, "Import danych — podsumowanie", "\n".join(results))
    return results
