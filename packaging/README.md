# Windows packaging

For this PyQt6 desktop application, the recommended release format is:

1. **PyInstaller one-folder build** for the executable and its Qt/Python
   dependencies.
2. **Inno Setup** for a normal Windows installer, shortcuts, uninstall support,
   and per-user installation.

From the repository root, create a fresh Windows Python 3.14 environment
(do not reuse a virtual environment created with an older Python):

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r requirements.lock
python -m pip install --require-hashes -r requirements-build.lock
python -m pip check
python -c "import mt940; from importlib.metadata import version; print('mt-940', version('mt-940')); assert callable(mt940.parse)"
python -m PyInstaller --clean --noconfirm packaging\biuro.spec
```

Then open `installer\biuro.iss` in Inno Setup and build it. The installer
expects the PyInstaller output in `dist\Biuro`.

Use the same Python environment for dependency installation and PyInstaller.
The distribution is named `mt-940`; its Python import is `mt940`. The spec
bundles its submodules, distribution metadata and supported bank-file encodings,
and stops if the parser is unavailable in the build environment.

Before building the installer, run `dist\Biuro\Biuro.exe` and import an ING
statement. Then repeat with the installed application. Keep the entire
`dist\Biuro` directory, including `_internal`; copying only the executable
does not produce a working application.

The application uses PostgreSQL. Configuration (`DB_URL` and the encryption
key) belongs in `%LOCALAPPDATA%\Biuro\.env`, outside the installation directory.
The installer preserves this configuration. After an application update, use
the **Biuro - aktualizacja bazy** shortcut to migrate the database before
logging in. An import failure dialog provides expandable exception details;
copy those details when reporting a failure that persists after rebuilding.

Runtime dependencies are resolved for Python 3.14 across platforms in
`requirements.lock`. Update them deliberately with
`uv pip compile requirements.txt --universal --python-version 3.14 --generate-hashes -o requirements.lock`,
then rerun the full suite before building. The spec includes the Alembic scripts,
psycopg pool, and IMAP UTF-7 codec. Exercise `Biuro.exe --migrate` twice: the second
run must be a no-op. The first Alembic upgrade of an older installation reuses the
frozen accounting bootstrap; subsequent changes belong in new revisions.
