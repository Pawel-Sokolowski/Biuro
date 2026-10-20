# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPEC).resolve().parent.parent
APP = ROOT / "app"

datas = [
    (str(APP / "icons"), "app/icons"),
]
CALDAV_PACKAGES = ["caldav", "icalendar", "icalendar_searcher", "recurring_ical_events", "niquests", "lxml"]
for pkg in CALDAV_PACKAGES:
    datas += collect_data_files(pkg)
# psycopg 3 + wbudowana libpq (psycopg_binary) - bez tego aplikacja nie polaczy sie z PostgreSQL
binaries = []
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
    datas=datas,
    hiddenimports=[
        *collect_submodules("app"),
        *PSYCOPG_HIDDEN,
        *[m for pkg in CALDAV_PACKAGES
          for m in collect_submodules(
              pkg, filter=lambda name: not name.startswith(("caldav.jmap", "niquests.extensions.pyodide")))],
        "PyQt6.QtCharts",
        "PyQt6.QtWebEngineCore",
        "PyQt6.QtWebEngineWidgets",
        "openpyxl",
        "bcrypt",
        "cryptography",
        "requests",
        "dotenv",
        "xlsxwriter",
        "dateutil",
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
    upx=True,
    console=False,
    exclude_binaries=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="Biuro",
)
