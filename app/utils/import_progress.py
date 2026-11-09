"""Run an accepted import off the GUI thread without reentrant event pumping."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QProgressDialog

from app.utils.background import CommandRunner


class _ImportProgress(QProgressDialog):
    progress = pyqtSignal(str)

    def __init__(self, parent):
        super().__init__('Przygotowanie importu…', None, 0, 0, parent)
        self.finished_work = False
        self.setWindowTitle('Import danych')
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumWidth(420)
        self.setMinimumDuration(0)
        self.setAutoClose(False)
        self.setAutoReset(False)
        self.progress.connect(self.setLabelText)

    def reject(self):
        if self.finished_work:
            super().reject()

    def closeEvent(self, event):
        if self.finished_work:
            super().closeEvent(event)
        else:
            event.ignore()


def run_import(work, parent=None):
    """work(progress) must only touch data; accepted writes finish before closing."""
    dialog = _ImportProgress(parent)
    runner = CommandRunner(dialog)
    outcome = {}

    def finish(value=None, error=None):
        outcome.update(value=value, error=error)
        dialog.finished_work = True
        dialog.accept()

    runner.submit(lambda: work(dialog.progress.emit), finish,
                  lambda exc: finish(error=exc))
    dialog.exec()
    dialog.deleteLater()
    if outcome.get('error') is not None:
        raise outcome['error']
    return outcome.get('value')


def confirm_company_import(path, parent=None):
    from PyQt6.QtWidgets import QMessageBox
    from app.utils.importers import prepare_company_import
    plan, prepared = run_import(lambda _: prepare_company_import(path), parent)
    text = (f"Nowe firmy: {plan['created']}\nAktualizacje: {plan['updated']}\n"
            f"Nieprawidłowe wiersze: {plan['invalid']}\nKonflikty: {plan['conflicts']}")
    if plan['warnings']:
        text += '\n\n' + '\n'.join(plan['warnings'][:20])
    if plan['invalid'] or plan['conflicts']:
        QMessageBox.warning(parent, 'Sprawdź plik przed importem', text + '\n\nPopraw błędy i ponów import.')
        prepared.close()
        return None
    accepted = QMessageBox.question(parent, 'Podsumowanie przed importem', text + '\n\nRozpocząć import?',
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
    if not accepted:
        prepared.close()
        return None
    return prepared
