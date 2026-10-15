# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPEC).resolve().parent.parent
APP = ROOT / "app"

datas = [
    (str(APP / "icons"), "app/icons"),
]
# Sekrety (.env) nie sa dolaczane do instalatora - patrz .env.example.

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        *collect_submodules("app"),
        "PyQt6.QtCharts",
        "openpyxl",
        "bcrypt",
        "cryptography",
        "requests",
        "dotenv",
        "xlsxwriter",
        "dateutil",
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
