# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPEC).resolve().parent.parent
APP = ROOT / "app"

datas = [
    (str(APP / "icons"), "app/icons"),
]
CALDAV_PACKAGES = ["caldav", "icalendar", "icalendar_searcher", "recurring_ical_events", "niquests", "lxml"]
for pkg in CALDAV_PACKAGES:
    datas += collect_data_files(pkg)
# Sekrety (.env) nie sa dolaczane do instalatora - patrz .env.example.

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        *collect_submodules("app"),
        *[m for pkg in CALDAV_PACKAGES for m in collect_submodules(pkg)],
        "PyQt6.QtCharts",
        "openpyxl",
        "bcrypt",
        "cryptography",
        "requests",
        "dotenv",
        "xlsxwriter",
        "dateutil",
        "imaplib",
        "smtplib",
        "sqlite3",
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
