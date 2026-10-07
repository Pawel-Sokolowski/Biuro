# PyInstaller build configuration for the Biuro desktop application.
#
# Recommended release flow:
#   1. pyinstaller packaging/biuro.spec
#   2. Package dist/Biuro with installer/biuro.iss using Inno Setup.

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPEC).resolve().parent.parent
APP = ROOT / "app"

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(APP / "icons"), "app/icons"),
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
