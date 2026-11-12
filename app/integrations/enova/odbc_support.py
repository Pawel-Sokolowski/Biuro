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


def validate_server(server):
    server = str(server or '').strip()
    if not server:
        raise ValueError('Uzupełnij serwer SQL.')
    if '/' in server:
        raise ValueError(r'Adres instancji SQL wymaga ukośnika wstecznego: np. 192.168.1.16\SQLEMKA, a nie 192.168.1.16/SQLEMKA. Możesz też użyć host,port.')
    return server


def describe_sql_error(exc, *, secrets=()):
    """Expose the actual SQLSTATE/driver detail without connection credentials."""
    import re
    raw = '\n'.join(str(arg) for arg in exc.args) or type(exc).__name__
    first = str(exc.args[0]) if exc.args else ''
    match = re.search(r'\[([A-Z0-9]{5})\]', raw)
    state = first if re.fullmatch(r'[A-Z0-9]{5}', first) else match[1] if match else ''
    lower = raw.casefold()
    if any(word in lower for word in ('certificate', 'certyfikat', 'certifikat', 'ssl provider')):
        hint = 'Błąd TLS/certyfikatu. Sprawdź zaufanie certyfikatu SQL Server i zgodność jego nazwy z adresem serwera.'
    elif any(code in raw for code in ('4060', '4064')):
        hint = 'SQL Server odrzucił wybraną lub domyślną bazę loginu. Sprawdź uprawnienia albo wpisz bazę firmy.'
    elif state == '28000' or '18456' in raw:
        hint = 'SQL Server odrzucił logowanie. Sprawdź login, hasło i włączenie uwierzytelniania SQL.'
    elif state in ('HYT00', 'HYT01'):
        hint = 'Przekroczono czas połączenia. Sprawdź TCP/IP, port SQL Server i zaporę sieciową.'
    elif state in ('08001', '08004', '08S01'):
        hint = 'Nie udało się połączyć z instancją SQL. Sprawdź TCP/IP, port i zaporę; instancja nazwana wymaga SQL Browser lub adresu host,port.'
    else:
        hint = 'SQL zgłosił błąd. Rozwiń szczegóły, aby zobaczyć komunikat sterownika.'
    for secret in secrets:
        if secret:
            for value in (str(secret).replace('}', '}}'), str(secret)):
                raw = raw.replace(value, '[ukryto]')
    raw = re.sub(r'(?i)\b(PWD|PASSWORD)\s*=\s*(\{(?:[^}]|}})*\}|[^;\r\n]*)',
                 r'\1=[ukryto]', raw)
    return {'sqlstate': state, 'hint': hint, 'details': raw[:4000]}
