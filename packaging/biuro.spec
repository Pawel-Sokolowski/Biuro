# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPEC).resolve().parent.parent
APP = ROOT / "app"

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Ikony
        (str(APP / "icons"), "app/icons"),

        # .env – MUSI być poziom wyżej niż packaging/
        (str(ROOT / ".env"), "."),

        # baza danych
        (str(ROOT / "biuro.db"), "."),
    ],
    hiddenimports=[
        *collect_submodules("app"),
        "PyQt6.QtCharts",
        "openpyxl",
        "xlsxwriter",
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
