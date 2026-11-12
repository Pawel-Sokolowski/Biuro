"""Local SQL Server ODBC capability detection, shared by desktop and worker."""
import sys

SUPPORTED_DRIVERS = ('ODBC Driver 18 for SQL Server', 'ODBC Driver 17 for SQL Server')


def load_odbc():
    try:
        import pyodbc
    except (ImportError, OSError):
        if getattr(sys, 'frozen', False):
            raise ValueError('W tej instalacji Biuro brakuje modułu połączenia SQL (pyodbc). Zainstaluj aktualną wersję Biuro. Sam sterownik Enova nie zastępuje tego modułu.') from None
        raise ValueError('Brak modułu pyodbc. Zainstaluj zależności requirements-enova.txt w środowisku uruchamiającym Biuro.') from None
    return pyodbc


def resolve_driver(config, odbc):
    installed = set(odbc.drivers())
    requested = config.get('driver', '').strip()
    if requested and requested.casefold() != 'auto':
        if requested not in SUPPORTED_DRIVERS:
            raise ValueError('Wybierz Microsoft ODBC Driver 18 lub 17 for SQL Server.')
        if requested not in installed:
            raise ValueError(f'Na tym komputerze nie zainstalowano {requested} dla architektury aplikacji. Wybierz Automatycznie lub zainstaluj ten sterownik.')
        return requested
    for driver in SUPPORTED_DRIVERS:
        if driver in installed:
            return driver
    raise ValueError('Na tym komputerze brakuje Microsoft ODBC Driver 18 lub 17 for SQL Server zgodnego z architekturą Biuro (zwykle x64). Enova może korzystać z własnego dostawcy .NET. Zainstaluj Microsoft ODBC Driver for SQL Server na tym komputerze.')
