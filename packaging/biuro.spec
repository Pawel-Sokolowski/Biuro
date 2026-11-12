# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

# Hidden imports alone only produce warnings when a dependency is missing.
# Stop the build instead of producing an executable without the bank parser.
try:
    import mt940
    if not callable(getattr(mt940, "parse", None)):
        raise ImportError("The installed mt940 module has no parse() function")
except ImportError as exc:
    raise SystemExit(
        "MT940 parser unavailable. Use the build interpreter to run: "
        "python -m pip install -r requirements.txt"
    ) from exc

try:
    import qrcode.image.pil
    import lxml.etree
    import docx
    import pypdf
    import pyzipper
    import Cryptodome.Cipher.AES
except ImportError as exc:
    raise SystemExit(
        "Invoice/contract PDF dependencies unavailable. Run: "
        "python -m pip install -r requirements.txt"
    ) from exc

def runtime_module(name):
    return not any(part in {'test', 'tests', 'examples', 'benchmarks'} for part in name.split('.'))


ROOT = Path(SPEC).resolve().parent.parent
APP = ROOT / "app"

datas = [
    (str(ROOT / 'docs' / 'enova'), 'docs/enova'),
    (str(APP / "icons"), "app/icons"),
    (str(APP / "documents" / "templates"), "app/documents/templates"),
    (str(APP / "db" / "migrations"), "app/db/migrations"),
    (str(APP / "invoices" / "schemas"), "app/invoices/schemas"),
]
CALDAV_PACKAGES = ["caldav", "icalendar", "icalendar_searcher", "recurring_ical_events", "niquests", "lxml", "tzdata"]
for pkg in CALDAV_PACKAGES:
    datas += collect_data_files(pkg)
# psycopg 3 + wbudowana libpq (psycopg_binary) - bez tego aplikacja nie polaczy sie z PostgreSQL
binaries = []
mt940_datas, mt940_binaries, mt940_hidden = collect_all("mt940")
datas += mt940_datas
datas += copy_metadata("mt-940")
binaries += mt940_binaries
PSYCOPG_HIDDEN = []
pkg_datas, pkg_binaries, pkg_hidden = collect_all("psycopg")
datas += pkg_datas
binaries += pkg_binaries
PSYCOPG_HIDDEN += pkg_hidden
# psycopg_binary nie daje sie zaimportowac samodzielnie (collect_all by zawiodl) - zbieramy go recznie
import importlib.util
binaries += collect_dynamic_libs("psycopg_binary")
datas += collect_data_files("psycopg_binary", include_py_files=True)
_binary_spec = importlib.util.find_spec("psycopg_binary")
if _binary_spec and _binary_spec.submodule_search_locations:
    _libs_dir = Path(list(_binary_spec.submodule_search_locations)[0]).parent / "psycopg_binary.libs"
    if _libs_dir.is_dir():
        binaries += [(str(p), "psycopg_binary.libs") for p in _libs_dir.glob("*.dll")]
PSYCOPG_HIDDEN += ["psycopg_binary", "psycopg_binary._psycopg", "psycopg_binary.pq", "psycopg_binary.version", "psycopg_binary._uuid"]# Sekrety (.env) nie sa dolaczane do instalatora - patrz .env.example.

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas + [(str(ROOT / 'app' / 'VERSION'), 'app')],
    hiddenimports=[
        *collect_submodules("app", filter=runtime_module),
        *mt940_hidden,
        "encodings.utf_8_sig",
        "encodings.cp1250",
        "encodings.iso8859_2",
        "encodings.cp852",
        *PSYCOPG_HIDDEN,
        "psycopg_pool",
        "pyodbc",
        "imapclient.imap_utf7",
        *collect_submodules('google.auth', filter=runtime_module),
        *collect_submodules('google.oauth2', filter=runtime_module),
        *collect_submodules('google_auth_oauthlib', filter=runtime_module),
        *collect_submodules('docxtpl', filter=runtime_module),
        *collect_submodules('stdnum.pl'),
        "sqlalchemy.dialects.postgresql.psycopg",
        *[m for pkg in CALDAV_PACKAGES
          for m in collect_submodules(
              pkg, filter=lambda name: runtime_module(name) and not name.startswith(("caldav.jmap", "niquests.extensions.pyodide")))],
        "PyQt6.QtCharts",
        "PyQt6.QtWebEngineCore",
        "PyQt6.QtWebEngineWidgets",
        "openpyxl",
        "winrt.windows.ui.notifications",
        "winrt.windows.data.xml.dom",
        "winrt.windows.storage.streams",
        "winrt.windows.ui.notifications.management",
        "winrt.windows.foundation",
        "winrt.windows.foundation.collections",
        "bcrypt",
        "cryptography",
        "requests",
        "pyzipper",
        "Cryptodome.Cipher.AES",
        "dotenv",
        "xlsxwriter",
        "dateutil",
        "qrcode.image.pil",
        "PIL.PngImagePlugin",
        "imaplib",
        "smtplib",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name="Biuro",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # Keep Chromium's Qt WebEngine helper process and libraries untouched.
    upx=False,
    console=False,
    exclude_binaries=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Biuro",
)
