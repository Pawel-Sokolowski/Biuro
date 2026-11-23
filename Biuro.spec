# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all


pyqt6_datas, pyqt6_binaries, pyqt6_hiddenimports = collect_all("PyQt6")


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=pyqt6_binaries,
    datas=pyqt6_datas,
    hiddenimports=pyqt6_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)


exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Biuro',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)


coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Biuro',
)