import os

import sys

from app.utils.env_config import load_environment, ensure_encryption_key

if __name__ == '__main__' and any(flag in sys.argv for flag in ('--worker', '--worker-panel', '--worker-setup', '--worker-install-task')):
    os.environ['BIO_WORKER_MODE'] = '1'
load_environment()
if __name__ == '__main__' and '--worker' in sys.argv:
    from app.worker import main as worker_main
    sys.exit(worker_main([arg for arg in sys.argv[1:] if arg != '--worker']))
# Hardware rendering is the normal path. Drivers that need the software
# fallback can opt in without rebuilding the installed application.
if os.environ.get("BIO_SOFTWARE_RENDERING") == "1" or os.environ.get("QT_OPENGL") == "software":
    os.environ.setdefault("QT_OPENGL", "software")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --disable-gpu-compositing")

from app.diagnostics import configure_logging
configure_logging()
from PyQt6.QtCore import Qt, QCoreApplication

if os.environ.get("QT_OPENGL") == "software":
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL)
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QApplication, QDialog)
import traceback

from app.utils.i18n import install_polish_translations

def excepthook(type, value, tb):
    import logging
    logging.getLogger('app').critical('uncaught_exception', exc_info=(type, value, tb))
    if sys.stderr is not None:
        sys.__excepthook__(type, value, tb)

sys.excepthook = excepthook

if __name__ == "__main__":
    if '--worker-install-task' in sys.argv:
        app = QApplication(sys.argv)
        install_polish_translations(app)
        from app.worker_install_ui import WorkerInstallDialog
        index = sys.argv.index('--worker-install-task')
        if index + 1 >= len(sys.argv):
            sys.exit(1)
        WorkerInstallDialog(sys.argv[index + 1]).exec()
        sys.exit(0)
    if '--worker-setup' in sys.argv or '--worker-panel' in sys.argv:
        app = QApplication(sys.argv)
        install_polish_translations(app)
        from app.worker_setup_ui import run
        sys.exit(run(app, configure='--worker-setup' in sys.argv))
    if '--smoke-test' in sys.argv:
        from app.release_smoke import run
        app = QApplication(sys.argv)
        sys.exit(run())
    if "--migrate" in sys.argv:
        # Biuro.exe --migrate: aktualizacja schematu bazy (bez okna logowania)
        from app.db.schema import upgrade_database, schema_is_current
        from PyQt6.QtWidgets import QMessageBox
        app = QApplication(sys.argv)
        try:
            upgrade_database()
            ok = schema_is_current()
            msg = "Baza danych jest aktualna." if ok else "Aktualizacja bazy nie powiodła się."
        except Exception as exc:
            import logging
            logging.getLogger('app').exception('database_migration_failed')
            ok, msg = False, f"Błąd aktualizacji bazy:\n{exc}"
        if '--non-interactive' not in sys.argv:
            (QMessageBox.information if ok else QMessageBox.critical)(None, "Aktualizacja bazy", msg)
        sys.exit(0 if ok else 1)

    from app.main_ui import LoginWindow, OfficeManagerApp

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    install_polish_translations(app)
    try:
        ensure_encryption_key()
    except ValueError:
        from app.key_setup_ui import KeySetupDialog
        if KeySetupDialog().exec() != QDialog.DialogCode.Accepted:
            sys.exit(0)
    login_window = LoginWindow()
    if login_window.exec() == QDialog.DialogCode.Accepted:
        pracownik_id = login_window.pracownik_id
        window = OfficeManagerApp(pracownik_id)
        window.restore_window_state()
        sys.exit(app.exec())
    else:
        sys.exit()
