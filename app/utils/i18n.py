from __future__ import annotations

from PyQt6.QtCore import QLibraryInfo, QLocale, QTranslator
from PyQt6.QtWidgets import QApplication

POLISH = QLocale(QLocale.Language.Polish, QLocale.Country.Poland)


def install_polish_translations(app: QApplication) -> list[QTranslator]:
    """Ustawia polską lokalizację i ładuje tłumaczenia standardowych okien Qt (Tak/Nie/Anuluj itd.)."""
    QLocale.setDefault(POLISH)
    translations = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    loaded: list[QTranslator] = []
    for name in ("qtbase", "qt"):
        translator = QTranslator(app)
        if translator.load(POLISH, name, "_", translations):
            app.installTranslator(translator)
            loaded.append(translator)
    return loaded
