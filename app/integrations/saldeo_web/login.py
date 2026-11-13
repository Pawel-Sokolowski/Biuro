def main():
    import os
    os.environ['BIO_WORKER_MODE'] = '1'
    from app.utils.env_config import load_environment
    load_environment()
    from PyQt6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication(['biuro-saldeo-login'])
    from app.main_ui import LoginWindow
    from app.security.authorization import is_administrator
    from PyQt6.QtWidgets import QDialog
    login = LoginWindow()
    if login.exec() != QDialog.DialogCode.Accepted or not is_administrator(login.pracownik_id):
        return 1
    try:
        from .browser import login_window
        login_window(actor_id=login.pracownik_id)
        return 0
    except ValueError as error:
        QMessageBox.warning(None, 'Saldeo', str(error))
        return 1
    except Exception:
        QMessageBox.warning(None, 'Saldeo', 'Nie otwarto sesji. Sprawdź zapisane ustawienia, zainstalowaną przeglądarkę Edge/Chrome i czy worker nie używa obecnie profilu.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
