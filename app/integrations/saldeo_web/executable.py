"""Resolve an installed browser independently of Playwright's channel assumptions."""
import os
from pathlib import Path


def registry_candidates(browser):
    try:
        import winreg
    except ImportError:
        return []
    result = []
    executable = 'chrome.exe' if browser=='chrome' else 'msedge.exe'
    for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive,r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\'+executable,
                                    0,winreg.KEY_READ|view) as key:
                    value,_ = winreg.QueryValueEx(key,None)
                    result.append(Path(str(value).strip('"')))
            except OSError:
                pass
    return result


def resolve(config):
    explicit = config.get('executable_path','').strip().strip('"')
    if explicit:
        path = Path(explicit)
        if not path.is_absolute() or not path.is_file() or path.suffix.lower()!='.exe':
            raise ValueError('Wskazany plik przeglądarki nie istnieje. Wybierz chrome.exe lub msedge.exe na tym komputerze.')
        return str(path)
    selected = config.get('channel','auto')
    if selected not in ('auto','chrome','msedge'):
        raise ValueError('Wybierz Chrome, Edge lub automatyczne wykrywanie.')
    for browser in (('chrome','msedge') if selected=='auto' else (selected,)):
        relative = 'Google/Chrome/Application/chrome.exe' if browser=='chrome' else 'Microsoft/Edge/Application/msedge.exe'
        candidates = registry_candidates(browser)
        for key in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA'):
            root = os.environ.get(key)
            if root:
                candidates.append(Path(root)/relative)
        for path in candidates:
            if path.is_file():
                return str(path)
    raise ValueError('Nie znaleziono przeglądarki dla tego konta Windows. '
                     'Wybierz Google Chrome / Microsoft Edge albo wskaż plik .exe przyciskiem Wybierz. '
                     'Instalacja Chrome na innym PC lub innym koncie użytkownika nie wystarcza dla workera.')
