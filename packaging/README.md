# Windows packaging

For this PyQt6 desktop application, the recommended release format is:

1. **PyInstaller one-folder build** for the executable and its Qt/Python
   dependencies.
2. **Inno Setup** for a normal Windows installer, shortcuts, uninstall support,
   and per-user installation.

From the repository root in a Windows Python environment:

```powershell
python -m pip install -r requirements.txt pyinstaller
pyinstaller --clean --noconfirm packaging\biuro.spec
```

Then open `installer\biuro.iss` in Inno Setup and build it. The installer
expects the PyInstaller output in `dist\Biuro`.

The SQLite database is selected on the login screen. The last successful path
is stored in Windows application settings under `Biuro/OfficeManager`, not in
the installation directory.
